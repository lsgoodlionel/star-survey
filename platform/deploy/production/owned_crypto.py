"""Typed, supervised OpenSSL child; its caller owns descriptor leases."""
from contextlib import contextmanager
import json
import math
import os
from pathlib import Path
import stat
import subprocess
import sys

from owned_children import read_process_identity


class KnownCryptoFailure(OSError):
    """No child was started, or this executor confirmed its child ended."""


class UncertainCryptoChild(RuntimeError):
    """Caller must retain pending state and descriptor leases, not claim cleanup."""


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
        return subprocess.Popen([sys.executable, '-I', '-B', str(script), str(actor['pid']), actor['start']],
            stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            pass_fds=tuple(fds), close_fds=True)


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
