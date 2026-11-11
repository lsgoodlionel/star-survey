"""Guarded helper identity and durable child transitions, not recovery authority."""
from copy import deepcopy
import ctypes
import json
import os
from pathlib import Path
import signal
import sys
import threading
import uuid


class ChildBoundaryError(RuntimeError):
    pass


def _pid(value):
    return type(value) is int and 0 < value < 2**31


def _start(value):
    return isinstance(value, str) and 1 <= len(value) <= 24 and value.isascii() and value.isdecimal()


def read_process_identity(pid):
    """Only kernel stat fields; never read cmdline, environ or process output."""
    if not _pid(pid) or sys.platform != 'linux':
        raise ChildBoundaryError('Linux process identity is unavailable')
    try:
        with (Path('/proc') / str(pid) / 'stat').open('rb') as handle:
            value = handle.read(4097)
    except (FileNotFoundError, ProcessLookupError):
        return None
    except OSError as exc:
        raise ChildBoundaryError('process identity could not be observed') from exc
    try:
        if len(value) > 4096:
            raise ValueError('oversized stat')
        text = value.decode('utf-8')
        number, separator, remaining = text.partition(' (')
        _, closing, tail = remaining.rpartition(') ')
        fields = tail.split()
        if not separator or not closing or int(number) != pid or len(fields) < 20:
            raise ValueError('invalid stat')
        parent, start = int(fields[1]), fields[19]
        if not 0 <= parent < 2**31 or not _start(start) or len(fields[0]) != 1:
            raise ValueError('invalid identity fields')
        return {'pid': pid, 'start': start, 'parentPid': parent, 'state': fields[0]}
    except (ValueError, UnicodeError, IndexError) as exc:
        raise ChildBoundaryError('process identity is malformed') from exc


def _linux_prctl():
    if sys.platform != 'linux' or threading.current_thread() is not threading.main_thread():
        raise ChildBoundaryError('controlled helper requires the Linux main thread')
    libc = ctypes.CDLL(None, use_errno=True)
    function = libc.prctl
    function.argtypes = [ctypes.c_int, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_ulong]
    function.restype = ctypes.c_int
    return function


def _parent_signal():
    value = ctypes.c_int()
    if _linux_prctl()(2, ctypes.addressof(value), 0, 0, 0) != 0:  # PR_GET_PDEATHSIG
        raise ChildBoundaryError('parent-death constraint is unavailable')
    return value.value


def establish_parent_guard(parent_pid, parent_start):
    if not _pid(parent_pid) or not _start(parent_start):
        raise ChildBoundaryError('expected parent identity is invalid')
    prctl = _linux_prctl()
    if prctl(1, signal.SIGKILL, 0, 0, 0) != 0 or prctl(38, 1, 0, 0, 0) != 0:
        raise ChildBoundaryError('helper kernel constraints were not established')
    current, parent = read_process_identity(os.getpid()), read_process_identity(parent_pid)
    if (current is None or parent is None or os.getppid() != parent_pid
            or current['parentPid'] != parent_pid or parent['start'] != parent_start
            or parent['state'] in ('Z', 'X')):
        raise ChildBoundaryError('expected parent died or changed before helper startup')
    guard = GuardedProcess({'pid': current['pid'], 'start': current['start'],
        'parentPid': parent_pid, 'parentStart': parent_start})
    guard.check()
    return guard


class GuardedProcess:
    def __init__(self, actor):
        self._actor = deepcopy(actor)

    def check(self):
        current = read_process_identity(os.getpid())
        parent = read_process_identity(self._actor['parentPid'])
        if (threading.current_thread() is not threading.main_thread() or current is None or parent is None
                or current['pid'] != self._actor['pid'] or current['start'] != self._actor['start']
                or current['parentPid'] != self._actor['parentPid'] or os.getppid() != self._actor['parentPid']
                or parent['start'] != self._actor['parentStart'] or parent['state'] in ('Z', 'X')
                or _parent_signal() != signal.SIGKILL or _linux_prctl()(39, 0, 0, 0, 0) != 1):
            raise ChildBoundaryError('controlled helper identity or parent-death guard changed')
        return deepcopy(self._actor)


class ChildJournal:
    """One guarded writer, bounded children, no adoption of a prior journal.

    `store` must be private/durable and covered by the installation lock. The
    production guard is obtained from establish_parent_guard, never from JSON.
    The executor still owns spawn, release, wait and termination responsibility.
    """
    PROFILES = {'openssl-encrypt', 'openssl-decrypt'}
    FINAL = {'completed', 'failed', 'not-started'}

    def __init__(self, store, guard, *, operation_id, installation_id, _child_reader=None):
        for value in (operation_id, installation_id):
            try:
                if not isinstance(value, str) or str(uuid.UUID(value)) != value:
                    raise ValueError('noncanonical identity')
            except (ValueError, AttributeError) as exc:
                raise ChildBoundaryError('journal operation binding is invalid') from exc
        self.store, self.guard = store, guard
        self._reader = _child_reader or read_process_identity
        self._owner = self._actor()
        self._record = None
        self._base = {'schemaVersion': 1, 'kind': 'controlled-helper-children',
            'operationId': operation_id, 'installationId': installation_id,
            'owner': self._owner, 'children': []}
        if self._call(self.store.load) is not None:
            raise ChildBoundaryError('an existing child journal cannot be resumed or replaced')

    @staticmethod
    def _call(function, *args):
        try:
            return function(*args)
        except Exception as exc:
            raise ChildBoundaryError('child boundary operation failed or its result is unknown') from exc

    def _actor(self):
        actor = self._call(self.guard.check)
        if (not isinstance(actor, dict) or set(actor) != {'pid', 'start', 'parentPid', 'parentStart'}
                or not _pid(actor['pid']) or not _pid(actor['parentPid']) or actor['pid'] == actor['parentPid']
                or not _start(actor['start']) or not _start(actor['parentStart'])):
            raise ChildBoundaryError('guarded writer identity is invalid')
        return deepcopy(actor)

    def _current(self):
        if self._actor() != self._owner or self._call(self.store.load) != self._record:
            raise ChildBoundaryError('child journal writer or stored state changed')
        return deepcopy(self._record if self._record is not None else self._base)

    def _write(self, record):
        self._current()
        if len(json.dumps(record, sort_keys=True).encode('utf-8')) > 65536:
            raise ChildBoundaryError('child journal exceeds its bounded size')
        self._call(self.store.save, record)
        if self._actor() != self._owner or self._call(self.store.load) != record:
            raise ChildBoundaryError('child journal persistence could not be verified')
        self._record = deepcopy(record)

    def _entry(self, record, token, phases):
        if type(token) is not int or not 1 <= token <= len(record['children']):
            raise ChildBoundaryError('child receipt token is invalid')
        entry = record['children'][token - 1]
        if entry['phase'] not in phases:
            raise ChildBoundaryError('child receipt phase is not eligible')
        return entry

    def _observed(self, pid):
        value = self._call(self._reader, pid)
        if value is not None and (not isinstance(value, dict) or set(value) != {'pid', 'start', 'parentPid', 'state'}
                or value['pid'] != pid or not _pid(value['pid']) or not _start(value['start'])
                or type(value['parentPid']) is not int or not 0 <= value['parentPid'] < 2**31
                or not isinstance(value['state'], str)
                or value['state'] not in {'R', 'S', 'D', 'T', 't', 'I', 'W', 'P', 'Z', 'X'}):
            raise ChildBoundaryError('child observation is malformed')
        return value

    def _live_child(self, pid, start):
        value = self._observed(pid)
        if (value is None or value['start'] != start or value['parentPid'] != self._owner['pid']
                or value['state'] in ('Z', 'X')):
            raise ChildBoundaryError('child identity is absent, ended or not owned by this helper')

    def begin(self, profile):
        record = self._current()
        if (not isinstance(profile, str) or profile not in self.PROFILES or len(record['children']) >= 64
                or any(entry['phase'] not in self.FINAL for entry in record['children'])):
            raise ChildBoundaryError('new child profile or cohort state is not eligible')
        token = len(record['children']) + 1
        record['children'].append({'sequence': token, 'profile': profile, 'phase': 'pending',
            'pid': None, 'start': None, 'exitCode': None, 'reason': None})
        self._write(record)
        return token

    def created(self, token, pid, start):
        record = self._current()
        entry = self._entry(record, token, {'pending'})
        if not _pid(pid) or pid == self._owner['pid'] or not _start(start):
            raise ChildBoundaryError('created child identity is invalid')
        self._live_child(pid, start)
        entry.update(phase='created', pid=pid, start=start)
        self._write(record)

    def released(self, token):
        record = self._current()
        entry = self._entry(record, token, {'created'})
        self._live_child(entry['pid'], entry['start'])
        entry['phase'] = 'released'
        self._write(record)

    def finished(self, token, exit_code, *, succeeded, reason):
        record = self._current()
        entry = self._entry(record, token, {'created', 'released'})
        if (type(exit_code) is not int or not -64 <= exit_code <= 255 or type(succeeded) is not bool
                or not isinstance(reason, str) or reason not in {'completed', 'exit', 'deadline', 'bootstrap', 'journal'}
                or (succeeded and (exit_code != 0 or reason != 'completed' or entry['phase'] != 'released'))
                or (not succeeded and reason == 'completed')):
            raise ChildBoundaryError('child completion status is invalid')
        observed = self._observed(entry['pid'])
        if observed is not None and observed['start'] == entry['start'] and observed['state'] not in ('Z', 'X'):
            raise ChildBoundaryError('child has not been confirmed ended')
        entry.update(phase='completed' if succeeded else 'failed', exitCode=exit_code, reason=reason)
        self._write(record)

    def not_started(self, token):
        record = self._current()
        entry = self._entry(record, token, {'pending'})
        entry.update(phase='not-started', reason='bootstrap')
        self._write(record)
