"""Internal candidate cipher adapter. Caller still binds package/source/operation.

No CLI entry point or legacy fallback. Unknown children retain their export;
the enclosing helper must keep this object alive and its operation pending.
"""
import os
from pathlib import Path
import re
import stat
import uuid

from backup import AuthenticatedOpenSSLCipher, BackupError, safe_absolute
from owned_crypto import KnownCryptoFailure, UncertainCryptoChild
from owned_memory import AnonymousFile, ChildArgument


class _Directory:
    def __init__(self, root):
        self.root, self.fd, self.identities, self.owned = root, None, {}, {}
        try:
            for path in (*reversed(root.parents), root):
                info = os.lstat(path)
                if (not stat.S_ISDIR(info.st_mode) or info.st_uid not in (0, os.geteuid())
                        or (info.st_mode & 0o022 and not (info.st_uid == 0 and info.st_mode & stat.S_ISVTX))
                        or (path == root and (info.st_uid != os.geteuid() or info.st_mode & 0o077))):
                    raise BackupError('cipher directory ancestry is unsafe')
                self.identities[path] = self.identity(info)
            self.fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
            self.check()
        except BaseException:
            if self.fd is not None:
                os.close(self.fd)
            raise

    @staticmethod
    def identity(info):
        return info.st_dev, info.st_ino, info.st_uid, info.st_mode

    def check(self):
        if (self.fd is None or self.identity(os.fstat(self.fd)) != self.identities[self.root]
                or any(self.identity(os.lstat(path)) != value for path, value in self.identities.items())):
            raise BackupError('cipher directory identity changed')

    def file(self, name):
        if not re.fullmatch(r'[a-z0-9.-]{1,100}\.enc(?:\.cipher)?', name):
            raise BackupError('cipher filename is invalid')
        return _File(self, name)

    def close(self):
        if self.fd is not None:
            if self.identity(os.fstat(self.fd)) != self.identities[self.root]:
                raise BackupError('cipher directory descriptor changed')
            os.close(self.fd)
            self.fd = None


class _File:
    def __init__(self, directory, name):
        self.directory, self.name = directory, name

    @property
    def suffix(self):
        return Path(self.name).suffix

    def with_suffix(self, suffix):
        return self.directory.file(Path(self.name).with_suffix(suffix).name)

    def child_argument(self):
        return self

    def open(self, mode):
        self.directory.check()
        if mode not in ('rb', 'xb'):
            raise BackupError('unsupported ciphertext file mode')
        flags = os.O_RDONLY if mode == 'rb' else os.O_WRONLY | os.O_CREAT | os.O_EXCL
        fd = os.open(self.name, flags | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK, 0o600, dir_fd=self.directory.fd)
        try:
            info = os.fstat(fd)
            binding = info.st_dev, info.st_ino
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid != os.geteuid() or info.st_mode & 0o077:
                raise BackupError('ciphertext file is unsafe')
            if mode == 'xb':
                self.directory.owned[self.name] = binding
            if self.name in self.directory.owned and self.directory.owned[self.name] != binding:
                raise BackupError('ciphertext file identity changed')
            self.directory.check()
            return os.fdopen(fd, mode)
        except BaseException:
            os.close(fd)
            raise

    def stat(self):
        with self.open('rb') as handle:
            return os.fstat(handle.fileno())

    def unlink(self, missing_ok=False):
        self.directory.check()
        try:
            info = os.stat(self.name, dir_fd=self.directory.fd, follow_symlinks=False)
        except FileNotFoundError:
            if missing_ok:
                return
            raise
        if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                or self.directory.owned.get(self.name) != (info.st_dev, info.st_ino)):
            raise BackupError('ciphertext cleanup cannot remove an unowned file')
        os.unlink(self.name, dir_fd=self.directory.fd)
        self.directory.owned.pop(self.name, None)


class _Runner:
    def __init__(self, workspace, executor, root, key):
        self.workspace, self.executor, self.root, self.key = workspace, executor, root, key
        self.held = None
        self.bridge = workspace / ('cipher-bridge-' + uuid.uuid4().hex)

    def run(self, command, **kwargs):
        if self.held is not None:
            raise UncertainCryptoChild('a prior crypto child remains unverified')
        if kwargs != {'check': True, 'capture_output': True} or not isinstance(command, list):
            raise BackupError('unsupported controlled cipher invocation')
        profile = 'decrypt' if command[:3] == ['openssl', 'enc', '-d'] else 'encrypt'
        prefix = (['openssl', 'enc', '-d', '-aes-256-cbc', '-pbkdf2', '-iter', '200000']
            if profile == 'decrypt' else ['openssl', 'enc', '-aes-256-cbc', '-pbkdf2', '-iter', '200000', '-salt'])
        if (len(command) != 13 or command[:7] != prefix or command[7::2] != ['-in', '-out', '-pass']
                or not isinstance(command[8], ChildArgument) or not isinstance(command[12], ChildArgument)
                or command[12].file is not self.key):
            raise BackupError('controlled cipher argument contract differs')
        source = command[8].file
        if not isinstance(source, AnonymousFile) or source._workspace is not self.workspace:
            raise BackupError('cipher input is outside the anonymous workspace')
        named_output = None
        if profile == 'decrypt':
            if not isinstance(command[10], ChildArgument):
                raise BackupError('plaintext output must be anonymous')
            output = command[10].file
            if not isinstance(output, AnonymousFile) or output._workspace is not self.workspace:
                raise BackupError('cipher output workspace differs')
        else:
            named_output = command[10]
            if (not isinstance(named_output, _File) or named_output.directory is not self.root
                    or not named_output.name.endswith('.enc.cipher')):
                raise BackupError('ciphertext destination is outside its private directory')
            output = self.bridge
        export = self.workspace.export_files(inputs=(source, self.key), output=output)
        leases = export.__enter__()
        self.held = export  # Strong reference before handing any descriptors to a child.
        try:
            result = self.executor.execute(profile, leases[0], leases[2], leases[1])
        except UncertainCryptoChild:
            raise  # Deliberately do not exit the lease on an unknown outcome.
        except KnownCryptoFailure:
            export.__exit__(None, None, None)
            self.held = None
            if named_output is not None:
                output.unlink(missing_ok=True)
            raise
        except BaseException as exc:
            raise UncertainCryptoChild('unexpected crypto boundary failure; leases retained') from exc
        export.__exit__(None, None, None)
        self.held = None
        if named_output is not None:
            try:
                with named_output.open('xb') as target, output.open('rb') as source_handle:
                    os.fchmod(target.fileno(), 0o600)
                    for block in iter(lambda: source_handle.read(1024 * 1024), b''):
                        target.write(block)
            finally:
                output.unlink(missing_ok=True)
        return result


class ControlledCipher(AuthenticatedOpenSSLCipher):
    def __init__(self, workspace, executor, encrypted_root, key_bytes):
        self.workspace = workspace
        self.root = safe_absolute(encrypted_root, 'controlled ciphertext directory')
        info = os.lstat(self.root)
        if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077
                or not isinstance(key_bytes, bytes) or not 32 <= len(key_bytes) <= 1024
                or key_bytes.strip() != key_bytes or any(value in key_bytes for value in (b'\n', b'\r', b'\x00'))):
            raise BackupError('controlled cipher directory or key is invalid')
        self.directory = _Directory(self.root)
        try:
            self.key = workspace / ('captured-key-' + uuid.uuid4().hex)
            with self.key.open('xb') as handle:
                handle.write(key_bytes)
            with self.key.export_for_child(write=False):
                pass  # Seal the captured key before any HMAC or child can observe it.
            self._runner = _Runner(workspace, executor, self.directory, self.key)
            super().__init__(runner=self._runner)
        except BaseException:
            self.directory.close()
            raise

    @property
    def uncertain(self):
        return self._runner.held is not None

    def _password_argument(self, key_file):
        if key_file is not self.key:
            raise BackupError('cipher key identity differs')
        return ChildArgument(self.key)

    def _validate(self, plain, encrypted, key):
        if self.uncertain:
            raise UncertainCryptoChild('a prior crypto child remains unverified')
        self.directory.check()
        if (key is not self.key or not isinstance(plain, AnonymousFile) or plain._workspace is not self.workspace
                or not isinstance(encrypted, Path) or encrypted.parent != self.root
                or not re.fullmatch(r'[a-z0-9.-]{1,100}\.enc', encrypted.name)):
            raise BackupError('controlled cipher operation binding differs')

    def _secure_output(self, destination):
        with destination.open('rb') as handle:
            os.fchmod(handle.fileno(), 0o600)

    def close(self):
        if self.uncertain:
            raise UncertainCryptoChild('cannot close a cipher with an unknown child')
        self.directory.close()

    def encrypt(self, source, destination, key_file):
        self._validate(source, destination, key_file)
        try:
            return super().encrypt(source, self.directory.file(destination.name), key_file)
        finally:
            if self.uncertain:
                raise UncertainCryptoChild('crypto outcome is unknown; descriptor leases retained')

    def decrypt(self, source, destination, key_file):
        self._validate(destination, source, key_file)
        try:
            return super().decrypt(self.directory.file(source.name), destination, key_file)
        finally:
            if self.uncertain:
                raise UncertainCryptoChild('crypto outcome is unknown; descriptor leases retained')
