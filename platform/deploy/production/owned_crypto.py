"""Typed, supervised OpenSSL child; its caller owns descriptor leases."""
from contextlib import contextmanager
import array
import json
import math
import os
from pathlib import Path
import stat
import socket
import subprocess
import sys
import time

from owned_children import read_process_identity


class KnownCryptoFailure(OSError):
    """No child was started, or this executor confirmed its child ended."""


class UncertainCryptoChild(RuntimeError):
    """Caller must retain pending state and descriptor leases, not claim cleanup."""


class GuardedCryptoProcess:
    """Spawn inherits one private control socket, never plaintext/key FDs.

    The exact child acknowledges its installed parent-death guard before the
    parent's released journal may hand over descriptors using SCM_RIGHTS.
    """
    READY = b'SURVEY-CRYPTO-GUARDED-1'

    def __init__(self, actor, descriptors, script):
        self.descriptors, self.control = tuple(descriptors), None
        parent, child = socket.socketpair(socket.AF_UNIX, socket.SOCK_SEQPACKET)
        self.control = parent
        try:
            if (len(self.descriptors) != 4 or len(set(self.descriptors)) != 4
                    or any(type(fd) is not int or fd <= 2 for fd in self.descriptors)
                    or min(parent.fileno(), child.fileno()) <= 2):
                raise KnownCryptoFailure('guarded descriptor set is invalid')
            self.args = [sys.executable, '-I', '-B', str(script), str(actor['pid']), actor['start'], str(child.fileno())]
            self.process = subprocess.Popen(self.args, stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                pass_fds=(child.fileno(),), close_fds=True)
        except BaseException:
            self.close_control()
            raise
        finally:
            child.close()

    @property
    def pid(self): return self.process.pid

    @property
    def returncode(self): return self.process.returncode

    def close_control(self):
        if self.control is not None:
            self.control.close()
            self.control = None

    def poll(self): return self.process.poll()

    def kill(self): return self.process.kill()

    def wait(self, timeout):
        result = self.process.wait(timeout=timeout)
        self.close_control()
        return result

    def communicate(self, input, timeout):
        if not isinstance(input, bytes) or not 1 <= len(input) <= 16384 or self.control is None:
            raise KnownCryptoFailure('guarded crypto envelope is invalid')
        deadline = time.monotonic() + timeout
        try:
            self.control.settimeout(min(5, timeout))
            if self.control.recv(64) != self.READY:
                raise KnownCryptoFailure('child did not acknowledge its installed guard')
            ancillary = [(socket.SOL_SOCKET, socket.SCM_RIGHTS, array.array('i', self.descriptors))]
            # This method is only called after the caller durably records the
            # released phase. A packet cannot expose a partial JSON/FD envelope.
            if self.control.sendmsg([input], ancillary) != len(input):
                raise KnownCryptoFailure('descriptor handoff was incomplete')
            remaining = deadline - time.monotonic()
            if remaining <= 0: raise subprocess.TimeoutExpired(self.args, timeout)
            self.process.wait(timeout=remaining)
            return b'', b''
        except socket.timeout as exc:
            raise subprocess.TimeoutExpired(self.args, timeout) from exc
        finally:
            self.close_control()


class LocalBackend:
    @contextmanager
    def binary(self):
        fd = os.open('/usr/bin/openssl', os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
        identity = os.fstat(fd)
        try:
            yield fd
        finally:
            current = os.fstat(fd)
            if (current.st_dev, current.st_ino) != (identity.st_dev, identity.st_ino):
                raise KnownCryptoFailure('binary descriptor ownership changed')
            os.close(fd)

    @staticmethod
    def describe(fd, role, profile):
        from owned_crypto_child import describe_fd
        return describe_fd(fd, role, profile)

    @staticmethod
    def identity(pid):
        return read_process_identity(pid)

    @staticmethod
    def spawn(actor, fds):
        script = Path(__file__).with_name('owned_crypto_child.py')
        info = os.lstat(script)
        if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                or info.st_uid not in (0, os.geteuid()) or info.st_mode & 0o022):
            raise KnownCryptoFailure('controlled crypto bootstrap is unsafe')
        return GuardedCryptoProcess(actor, fds, script)


class OpenSSLProcess:
    def __init__(self, journal, guard, *, _backend=None):
        self.journal, self.guard = journal, guard
        self.backend = _backend or LocalBackend()

    def _confirm_ended(self, process, start):
        try:
            observed = self.backend.identity(process.pid)
        except Exception as exc:
            raise UncertainCryptoChild('controlled child end observation is unavailable') from exc
        if observed is not None and (start is None or observed.get('start') == start):
            if observed.get('state') not in ('Z', 'X'):
                raise UncertainCryptoChild('controlled child remains live')

    def _end(self, process, start):
        # A kill error must not skip wait: it can race a normal child exit.
        try:
            if process.poll() is None:
                process.kill()
        except OSError:
            pass
        try:
            code = process.wait(timeout=5)
            if type(code) is not int or not -64 <= code <= 255:
                raise ValueError('child result unavailable')
            self._confirm_ended(process, start)
            return code
        except Exception as exc:
            raise UncertainCryptoChild('controlled child termination is unverified') from exc

    def execute(self, profile, input_lease, output_lease, key_lease, *, timeout=60):
        process, token, ended, code, child_start = None, None, False, None, None
        reason = 'bootstrap'
        try:
            if (not isinstance(profile, str) or profile not in ('encrypt', 'decrypt')
                    or type(timeout) not in (int, float) or not math.isfinite(timeout) or not 0.001 <= timeout <= 300
                    or any(os.environ.get(name) for name in ('OPENSSL_CONF', 'OPENSSL_MODULES', 'OPENSSL_ENGINES'))):
                raise ValueError('unsupported crypto profile or environment')
            leases = {'input': input_lease, 'output': output_lease, 'key': key_lease}
            fds = [lease.fd for lease in leases.values()]
            if (any(type(fd) is not int or fd <= 2 for fd in fds) or len(set(fds)) != 3
                    or type(output_lease.max_file_bytes) is not int
                    or not 1 <= output_lease.max_file_bytes <= 2**30 + 64):
                raise ValueError('invalid descriptor lease')
            actor = self.guard.check()
            with self.backend.binary() as binary:
                if type(binary) is not int or binary <= 2 or binary in fds:
                    raise ValueError('binary descriptor aliases data')
                descriptions = {role: self.backend.describe(lease.fd, role, profile) for role, lease in leases.items()}
                descriptions['binary'] = self.backend.describe(binary, 'binary', profile)
                if len({(entry['dev'], entry['ino']) for entry in descriptions.values()}) != 4:
                    raise ValueError('aliased file identities')
                payload = json.dumps({'schemaVersion': 1, 'profile': profile, 'fds': descriptions,
                    'maxOutputBytes': output_lease.max_file_bytes, 'timeoutSeconds': math.ceil(timeout)}).encode('utf-8')
                if len(payload) > 16384:
                    raise ValueError('oversized envelope')
                token = self.journal.begin('openssl-' + profile)
                process = self.backend.spawn(actor, (*fds, binary))
                observed = self.backend.identity(process.pid)
                if observed is None:
                    raise KnownCryptoFailure('child startup identity is unavailable')
                child_start = observed['start']
                self.journal.created(token, process.pid, child_start)
                self.journal.released(token)  # Durable before the stdin start gate.
                reason = 'exit'
                try:
                    process.communicate(input=payload, timeout=timeout)
                except subprocess.TimeoutExpired:
                    reason = 'deadline'
                    code = self._end(process, child_start)
                    ended = True
                    raise KnownCryptoFailure('controlled crypto deadline exceeded')
                code = process.returncode
                if type(code) is not int:
                    raise UncertainCryptoChild('controlled child exit is unknown')
                self._confirm_ended(process, child_start)
                ended = True
                if code != 0:
                    raise KnownCryptoFailure('controlled crypto child failed')
                for role, before in descriptions.items():
                    after = self.backend.describe(before['fd'], role, profile)
                    keys = ('fd', 'dev', 'ino', 'uid', 'mode', 'nlink', 'kind')
                    if any(before[key] != after[key] for key in keys):
                        raise KnownCryptoFailure('crypto descriptor identity changed')
                    if role == 'output':
                        if after['size'] > output_lease.max_file_bytes:
                            raise KnownCryptoFailure('crypto output exceeded its limit')
                    elif (before['size'], before['mtime']) != (after['size'], after['mtime']):
                        raise KnownCryptoFailure('crypto input or binary changed')
                self.guard.check()
                self.journal.finished(token, code, succeeded=True, reason='completed')
                return subprocess.CompletedProcess(['openssl', 'enc', profile], 0, stdout=b'', stderr=b'')
        except UncertainCryptoChild:
            raise
        except Exception as exc:
            if process is not None and not ended:
                code = self._end(process, child_start)
                ended = True
            if token is not None:
                try:
                    if process is None:
                        self.journal.not_started(token)
                    else:
                        self.journal.finished(token, code, succeeded=False, reason=reason)
                except Exception:
                    pass  # Original pending/phase remains; never report success.
            raise KnownCryptoFailure('controlled crypto did not complete successfully') from exc
