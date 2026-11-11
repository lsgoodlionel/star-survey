"""Anonymous plaintext workspace for the controlled helper, not a disk fallback.

The production backend requires Linux memfd and kernel write seals. Exported
descriptors must only go to the supervised child with an enforced RLIMIT_FSIZE;
the lease does not itself start, stop or prove termination of that child.
"""
from contextlib import contextmanager
import fcntl
import io
import os
from pathlib import PurePosixPath
import stat
import sys
import threading
import uuid


class WorkspaceError(RuntimeError):
    pass


class LinuxMemfdBackend:
    def __init__(self):
        if sys.platform != 'linux' or not callable(getattr(os, 'memfd_create', None)):
            raise WorkspaceError('anonymous plaintext requires Linux memfd; disk fallback is forbidden')
        if not all(hasattr(fcntl, name) for name in ('F_ADD_SEALS', 'F_SEAL_WRITE', 'F_SEAL_GROW', 'F_SEAL_SHRINK', 'F_SEAL_SEAL')):
            raise WorkspaceError('anonymous plaintext requires kernel write seals')

    def create(self, name):
        return os.memfd_create(name, os.MFD_CLOEXEC | os.MFD_ALLOW_SEALING)

    def seal(self, fd):
        fcntl.fcntl(fd, fcntl.F_ADD_SEALS,
            fcntl.F_SEAL_WRITE | fcntl.F_SEAL_GROW | fcntl.F_SEAL_SHRINK | fcntl.F_SEAL_SEAL)


class DescriptorLease:
    __slots__ = ('fd', 'path', 'max_file_bytes')
    def __init__(self, fd, maximum):
        self.fd, self.path, self.max_file_bytes = fd, '/proc/self/fd/' + str(fd), maximum


class ChildArgument:
    """An opaque capability. Ordinary subprocess/path APIs must reject it."""
    __slots__ = ('file',)
    def __init__(self, file):
        self.file = file

    def __str__(self):
        raise WorkspaceError('anonymous arguments require a supervised descriptor export')


class AnonymousWorkspace:
    def __init__(self, *, max_bytes=256 * 1024 * 1024, max_files=32, _backend=None):
        if (type(max_bytes) is not int or not 1 <= max_bytes <= 1024 * 1024 * 1024
                or type(max_files) is not int or not 1 <= max_files <= 128):
            raise WorkspaceError('anonymous workspace limits are invalid')
        self.max_bytes, self.max_files = max_bytes, max_files
        self._backend = _backend if _backend is not None else LinuxMemfdBackend()
        self._entries, self._closed, self._close_failed = {}, False, False
        self._exports, self._handles = 0, 0
        self._lock = threading.RLock()
        self._nonce = uuid.uuid4().hex

    def _live(self):
        if self._closed or self._close_failed:
            raise WorkspaceError('anonymous workspace is closed or its ownership is unknown')

    @staticmethod
    def _identity(info):
        return info.st_dev, info.st_ino, info.st_uid, stat.S_IMODE(info.st_mode)

    def _check(self, entry):
        self._live()
        if entry['fd'] is None:
            raise FileNotFoundError('anonymous file is absent')
        try:
            info = os.fstat(entry['fd'])
        except OSError as exc:
            raise WorkspaceError('anonymous descriptor ownership is unavailable') from exc
        if (self._identity(info) != entry['identity'] or not stat.S_ISREG(info.st_mode)
                or info.st_nlink != 0 or info.st_uid != os.geteuid() or info.st_mode & 0o077):
            raise WorkspaceError('anonymous descriptor ownership changed')
        return info

    def _total(self):
        return sum(self._check(entry).st_size for entry in self._entries.values() if entry['fd'] is not None)

    def __truediv__(self, name):
        with self._lock:
            self._live()
            if (not isinstance(name, str) or not name or len(name) > 128 or name in ('.', '..')
                    or any(char in name for char in ('/', '\\', '\x00')) or PurePosixPath(name).name != name):
                raise WorkspaceError('anonymous file name is invalid')
            if name not in self._entries:
                if len(self._entries) >= self.max_files or self._exports:
                    raise WorkspaceError('anonymous file set is full or exported')
                self._entries[name] = {'fd': None, 'identity': None, 'sealed': False, 'handles': 0}
            return AnonymousFile(self, name, self._entries[name])

    def _create(self, entry):
        self._live()
        if self._exports:
            raise WorkspaceError('cannot allocate during a child export')
        fd = self._backend.create('survey-plain-' + self._nonce)
        try:
            os.set_inheritable(fd, False)
            os.fchmod(fd, 0o600)
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 0 or info.st_size != 0:
                raise WorkspaceError('backend did not create an empty anonymous file')
            entry.update(fd=fd, identity=self._identity(info), sealed=False)
            self._check(entry)
        except BaseException:
            os.close(fd)
            entry.update(fd=None, identity=None)
            raise

    @contextmanager
    def export_files(self, *, inputs=(), output=None):
        """One synchronous child operation; caller enforces the returned limits."""
        with self._lock:
            self._live()
            if self._exports or self._handles:
                raise WorkspaceError('workspace has an active file or child operation')
            paths = list(inputs) + ([] if output is None else [output])
            if (not paths or any(not isinstance(path, AnonymousFile) or path._workspace is not self for path in paths)
                    or len({path.name for path in paths}) != len(paths)):
                raise WorkspaceError('child descriptor set is invalid')
            leases = []
            for path in paths:
                entry = path._entry
                if entry['fd'] is None and path is output:
                    self._create(entry)
                info = self._check(entry)
                if path is output:
                    if entry['sealed']:
                        raise WorkspaceError('child output is sealed')
                    maximum = self.max_bytes - self._total() + info.st_size
                    if maximum <= 0:
                        raise WorkspaceError('anonymous plaintext budget is exhausted')
                else:
                    if not entry['sealed']:
                        self._backend.seal(entry['fd'])
                        entry['sealed'] = True
                    maximum = info.st_size
                leases.append(DescriptorLease(entry['fd'], maximum))
            self._exports = 1
        try:
            yield tuple(leases)
        finally:
            with self._lock:
                self._exports = 0
                # A child result is not accepted merely because it returned.
                for path, lease in zip(paths, leases):
                    if self._check(path._entry).st_size > lease.max_file_bytes:
                        raise WorkspaceError('child exceeded its anonymous file budget')
                if self._total() > self.max_bytes:
                    raise WorkspaceError('child exceeded the anonymous workspace budget')

    def close(self):
        with self._lock:
            if self._closed:
                return
            if self._close_failed or self._exports or self._handles:
                raise WorkspaceError('workspace cleanup is unverified or still in use')
            failed = False
            for entry in self._entries.values():
                if entry['fd'] is None:
                    continue
                try:
                    self._check(entry)
                    os.close(entry['fd'])
                    entry.update(fd=None, identity=None)
                except (OSError, WorkspaceError):
                    # Never close a descriptor that was replaced by another file.
                    failed = True
            self._close_failed = failed
            if failed:
                raise WorkspaceError('anonymous descriptor cleanup could not be verified')
            self._closed = True

    def __enter__(self):
        self._live()
        return self

    def __exit__(self, *unused):
        self.close()


class AnonymousFile:
    def __init__(self, workspace, name, entry):
        self._workspace, self.name, self._entry = workspace, name, entry

    def child_argument(self):
        self._workspace._live()
        return ChildArgument(self)

    def __str__(self):
        raise WorkspaceError('anonymous files are not named filesystem paths')

    @property
    def suffix(self):
        return PurePosixPath(self.name).suffix

    def with_suffix(self, suffix):
        return self._workspace / PurePosixPath(self.name).with_suffix(suffix).name

    def stat(self):
        with self._workspace._lock:
            return self._workspace._check(self._entry)

    def open(self, mode='rb'):
        workspace, entry = self._workspace, self._entry
        with workspace._lock:
            workspace._live()
            if workspace._exports or mode not in ('rb', 'xb', 'wb'):
                raise WorkspaceError('anonymous open mode or child state is unsupported')
            if mode == 'xb' and entry['fd'] is not None:
                raise FileExistsError('anonymous file already exists')
            if entry['fd'] is None:
                if mode == 'rb':
                    raise FileNotFoundError('anonymous file is absent')
                workspace._create(entry)
            workspace._check(entry)
            if mode != 'rb' and entry['sealed']:
                raise WorkspaceError('anonymous input is sealed')
            if mode == 'wb':
                os.ftruncate(entry['fd'], 0)
            workspace._handles += 1
            entry['handles'] += 1
            return BoundedHandle(self, mode != 'rb')

    def read_bytes(self):
        with self.open('rb') as handle:
            return handle.read()

    def unlink(self, missing_ok=False):
        workspace, entry = self._workspace, self._entry
        with workspace._lock:
            workspace._live()
            if workspace._exports or entry['handles']:
                raise WorkspaceError('anonymous file is still in use')
            if entry['fd'] is None:
                if missing_ok:
                    return
                raise FileNotFoundError('anonymous file is absent')
            workspace._check(entry)
            os.close(entry['fd'])
            entry.update(fd=None, identity=None, sealed=False)

    @contextmanager
    def export_for_child(self, *, write):
        if type(write) is not bool:
            raise WorkspaceError('child file access mode is invalid')
        with self._workspace.export_files(inputs=() if write else (self,), output=self if write else None) as leases:
            yield leases[0]


class BoundedHandle(io.RawIOBase):
    def __init__(self, path, writable):
        super().__init__()
        self._path, self._writable, self._position = path, writable, 0

    def writable(self):
        return self._writable

    def readable(self):
        return not self._writable

    def seekable(self):
        return True

    def tell(self):
        self._checkClosed()
        return self._position

    def seek(self, offset, whence=io.SEEK_SET):
        self._checkClosed()
        with self._path._workspace._lock:
            info = self._path.stat()
            bases = {io.SEEK_SET: 0, io.SEEK_CUR: self._position, io.SEEK_END: info.st_size}
            if whence not in bases or type(offset) is not int or not 0 <= bases[whence] + offset <= self._path._workspace.max_bytes:
                raise WorkspaceError('anonymous seek is outside the budget')
            self._position = bases[whence] + offset
            return self._position

    def read(self, size=-1):
        self._checkClosed()
        if self._writable:
            raise io.UnsupportedOperation('not readable')
        workspace, entry = self._path._workspace, self._path._entry
        with workspace._lock:
            info = workspace._check(entry)
            if info.st_size > workspace.max_bytes:
                raise WorkspaceError('anonymous file exceeds its budget')
            count = max(0, info.st_size - self._position)
            if size != -1:
                if type(size) is not int or size < 0:
                    raise ValueError('invalid read size')
                count = min(count, size)
            data = os.pread(entry['fd'], count, self._position)
            self._position += len(data)
            return data

    def readinto(self, buffer):
        data = self.read(len(buffer))
        buffer[:len(data)] = data
        return len(data)

    def write(self, data):
        self._checkClosed()
        if not self._writable:
            raise io.UnsupportedOperation('not writable')
        workspace, entry = self._path._workspace, self._path._entry
        value = memoryview(data)
        with workspace._lock:
            info = workspace._check(entry)
            if workspace._exports or entry['sealed']:
                raise WorkspaceError('anonymous file is exported or sealed')
            growth = max(0, self._position + value.nbytes - info.st_size)
            if workspace._total() + growth > workspace.max_bytes:
                raise WorkspaceError('anonymous plaintext budget is exhausted')
            count = os.pwrite(entry['fd'], value, self._position)
            self._position += count
            if count != value.nbytes:
                raise WorkspaceError('anonymous write did not complete')
            return count

    def close(self):
        if not self.closed:
            with self._path._workspace._lock:
                self._path._workspace._handles -= 1
                self._path._entry['handles'] -= 1
            super().close()
