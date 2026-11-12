"""Held-FD reservation -> bounded runtime-record filesystem phase, not restore.

Persistent pending remains after every close/error. Resource/receipt contents,
source authentication, target signatures/config and execution admission are NOT
qualified here. Those independent checks must precede any future import.
Trusted exclusive controller only; no hostile same-UID or atomic TOCTOU claim.
"""
import hashlib
import json
import os
import re
import stat

from owned_docker_exec import UncertainExec
from recovery_reservation import RecoveryReservation, ReservationError, _identity, _snapshot
from runtime_ownership import PrivateStore


class HandoffError(ReservationError, UncertainExec):
    """Pending handoff remains; never classify unknown release as a plain I/O error."""


class ReservationHandoff:
    MARKER = 'recovery.handoff.json'

    def __init__(self, guard, receipts):
        if (not isinstance(guard, RecoveryReservation) or type(receipts) is not tuple
                or not 1 <= len(receipts) <= 16
                or any(not isinstance(name, str) or re.fullmatch(r'runtime-(?:exec|child)-[0-9a-f]{32}\.json', name) is None
                       for name in receipts) or len(set(receipts)) != len(receipts)):
            raise ReservationError('runtime receipt plan is invalid')
        guard.inspect()  # Original strict three-file inventory, before any mutation.
        self.path, self.nonce, self.source = guard.path, guard.nonce, guard.source
        self._target, self._control = guard._target, guard._control
        self._directories, self._files = tuple(guard._directories), dict(guard._files)
        self.receipts = tuple(receipts)
        self._active, self._failed, self._fds = False, False, []
        self._base = RecoveryReservation.FILES | {self.MARKER}
        self._allowed = self._base | {'runtime-owner.json', *receipts}
        self.data = (json.dumps({'schemaVersion': 1, 'state': 'pending-not-authorized',
            'reservationId': self.nonce, 'projectProposal': 'survey-recovery-' + self.nonce,
            'sourceObservationSha256': self.source, 'plannedReceipts': list(receipts),
            'executable': False}, sort_keys=True, separators=(',', ':')) + '\n').encode()
        try:
            # The writer is private and never inherited/exported. Keep the same
            # inode open for rechecks; no replace, adoption or marker deletion.
            fd = os.open(self.MARKER, os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600,
                         dir_fd=self._control)
            self._fds.append(fd); os.set_inheritable(fd, False); os.fchmod(fd, 0o600)
            if os.write(fd, self.data) != len(self.data): raise HandoffError('handoff marker write was incomplete')
            os.fsync(fd); os.fsync(self._control)
            info = os.fstat(fd)
            if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                    or info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o600):
                raise HandoffError('handoff marker identity is unsafe')
            self._marker = (fd, _snapshot(info), hashlib.sha256(self.data).digest())
            self._check(initial=True)
        except BaseException as exc:
            guard._failed = True  # Never reuse an interrupted transition in this controller.
            try: self.close()
            except HandoffError as close_error: raise close_error from exc
            if isinstance(exc, HandoffError): raise
            raise HandoffError('handoff preparation failed; preserve pending target') from exc
        # No I/O after ownership transfer: outer guard exit cannot unlock/close
        # the original descriptors, even if this context outlives that exit.
        descriptors = [*guard._fds, *self._fds]
        guard._fds = []
        guard._active, guard._transferred = False, True
        self._fds, self._active = descriptors, True

    def _anchors(self):
        for parent, name, fd, expected in self._directories:
            observed = os.stat(name, follow_symlinks=False, **({'dir_fd': parent} if parent is not None else {}))
            if _identity(observed) != expected or _identity(os.fstat(fd)) != expected:
                raise HandoffError('handoff directory identity changed')
        for name, (fd, expected, digest) in {**self._files, self.MARKER: self._marker}.items():
            if (_snapshot(os.fstat(fd)) != expected
                    or _snapshot(os.stat(name, dir_fd=self._control, follow_symlinks=False)) != expected
                    or hashlib.sha256(os.pread(fd, expected[6] + 1, 0)).digest() != digest
                    or _snapshot(os.fstat(fd)) != expected
                    or _snapshot(os.stat(name, dir_fd=self._control, follow_symlinks=False)) != expected):
                raise HandoffError('handoff anchor contents or identity changed')

    def _inventory(self, *, initial=False, read_records=True):
        observed = []
        for fd, required, allowed in ((self._target, {'.surveyctl'}, {'.surveyctl'}),
                                      (self._control, self._base, self._base if initial else self._allowed)):
            found = set()
            with os.scandir(fd) as entries:
                for entry in entries:
                    if entry.name not in allowed or entry.name in found:
                        raise HandoffError('handoff inventory is unknown')
                    found.add(entry.name)
            if not required <= found: raise HandoffError('handoff anchor is missing')
            if fd == self._control and read_records:
                for name in found - self._base:
                    # Only filesystem/JSON observation. OwnershipProtocol and
                    # CohortProbe must separately validate these semantics.
                    store = PrivateStore(self.path) if name == 'runtime-owner.json' else PrivateStore(self.path, name=name)
                    if not isinstance(store.load(), dict):
                        raise HandoffError('handoff runtime record is unreadable')
            observed.append(frozenset(found))

        return tuple(observed)

    def _check(self, *, initial=False):
        self._anchors()
        observed = self._inventory(initial=initial)
        # Runtime JSON reads must not hide a replacement of an immutable
        # anchor or late inventory change before this observation returns.
        self._anchors()
        if observed != self._inventory(initial=initial, read_records=False):
            raise HandoffError('handoff inventory changed while reading records')

    def inspect(self):
        if not self._active or self._failed: raise HandoffError('handoff is inactive or failed')
        try:
            self._check()
            return {'schemaVersion': 1, 'status': 'handoff-filesystem-bound-not-authorized',
                'executable': False, 'runtimeRecordsQualified': False, 'backupAuthenticated': False,
                'targetSignatureVerified': False, 'restoreAuthorized': False,
                'reservationId': self.nonce, 'projectProposal': 'survey-recovery-' + self.nonce}
        except BaseException as exc:
            self._failed = True
            if isinstance(exc, HandoffError): raise
            raise HandoffError('handoff recheck failed; preserve pending target') from exc

    def close(self):
        self._active = False
        descriptors, self._fds = self._fds, []
        failure = None
        for fd in reversed(descriptors):
            try: os.close(fd)
            except OSError as exc: failure = exc
        if failure is not None:
            self._failed = True
            raise HandoffError('handoff descriptor release is unknown; preserve pending target') from failure

    def __enter__(self):
        try:
            self.inspect()
        except BaseException as original:
            # Python will not call __exit__ after a failed __enter__.
            try: self.close()
            except HandoffError as close_error: raise close_error from original
            raise
        return self

    def __exit__(self, kind, value, traceback):
        failure = value
        try:
            if kind is None: self.inspect()
        except BaseException as exc:
            failure = exc; raise
        finally:
            try: self.close()
            except HandoffError as close_error:
                if failure is not None: raise close_error from failure
                raise
