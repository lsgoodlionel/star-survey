"""Internal read-only guard for an unpublished recovery executor.

Only pins an empty, reserved filesystem target under the trusted exclusive
controller model. No Docker resources, source authentication, restore or CLI
authorization. A source observation hash is a binding, never authentication.
Same-UID hostile mutation and the final check-to-use interval are not fenced.
"""
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import stat


class ReservationError(Exception):
    pass


def _identity(info):
    return info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_gid


def _snapshot(info):
    return _identity(info) + (info.st_nlink, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ReservationError('recovery reservation has duplicate fields')
        result[key] = value
    return result


class RecoveryReservation:
    """Single-use context, holding the existing lock until final recheck.

    Never creates a lock or repairs/deletes an interrupted reservation. All
    descriptors remain private and noninheritable. Call inspect before a future
    consumer's use; neither inspection nor this context is an execution grant.
    """
    FILES = frozenset({'recovery.reservation.json', 'operation.lock', 'operation.intent'})

    def __init__(self, destination, reservation_id, source_observation_sha256):
        self.path = Path(destination)
        if (not self.path.is_absolute() or '..' in self.path.parts
                or not isinstance(reservation_id, str) or re.fullmatch('[0-9a-f]{32}', reservation_id) is None
                or not isinstance(source_observation_sha256, str)
                or re.fullmatch('[0-9a-f]{64}', source_observation_sha256) is None):
            raise ReservationError('recovery reservation binding is invalid')
        self.nonce, self.source = reservation_id, source_observation_sha256
        self._fds, self._directories, self._files = [], [], {}
        self._used = self._active = False
        self._failed = False
        self._transferred = False

    def _directory(self, name, parent=None, *, ancestor=False):
        fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                     **({'dir_fd': parent} if parent is not None else {}))
        self._fds.append(fd)
        info = os.fstat(fd)
        owners = {0, os.geteuid()} if ancestor else {os.geteuid()}
        shared = info.st_mode & 0o022
        sticky_root = ancestor and info.st_uid == 0 and info.st_mode & stat.S_ISVTX
        if (not stat.S_ISDIR(info.st_mode) or info.st_uid not in owners
                or (shared and not sticky_root)
                or (not ancestor and stat.S_IMODE(info.st_mode) != 0o700)):
            raise ReservationError('recovery reservation directory is unsafe')
        self._directories.append((parent, name, fd, _identity(info)))
        return fd

    def _read(self, name, bound):
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=self._control)
        self._fds.append(fd)
        before = os.fstat(fd)
        if (not stat.S_ISREG(before.st_mode) or before.st_nlink != 1
                or before.st_uid != os.geteuid() or stat.S_IMODE(before.st_mode) != 0o600
                or before.st_size > bound):
            raise ReservationError('recovery reservation file is unsafe')
        data = bytearray()
        while len(data) <= bound:
            part = os.read(fd, bound + 1 - len(data))
            if not part: break
            data.extend(part)
        if len(data) > bound or _snapshot(os.fstat(fd)) != _snapshot(before):
            raise ReservationError('recovery reservation changed while reading')
        self._files[name] = (fd, _snapshot(before), hashlib.sha256(data).digest())
        return bytes(data)

    @staticmethod
    def _json(data):
        try:
            value = json.loads(data, object_pairs_hook=_object)
        except (ValueError, UnicodeError) as exc:
            raise ReservationError('recovery reservation JSON is invalid') from exc
        if not isinstance(value, dict):
            raise ReservationError('recovery reservation must be an object')
        return value

    def _validate(self, record, intent):
        keys = {'schemaVersion', 'state', 'executable', 'reservationId', 'projectProposal',
                'sourceObservationSha256', 'targetIdentity', 'controlIdentity',
                'backupAuthenticated', 'runtimeResourcesCreated'}
        if (set(record) != keys or type(record['schemaVersion']) is not int or record['schemaVersion'] != 1
                or record['state'] != 'reserved-not-authorized'
                or any(record[name] is not False for name in ('executable', 'backupAuthenticated', 'runtimeResourcesCreated'))
                or record['reservationId'] != self.nonce
                or record['sourceObservationSha256'] != self.source
                or record['projectProposal'] != 'survey-recovery-' + self.nonce):
            raise ReservationError('recovery reservation binding differs')
        for name, fd in (('targetIdentity', self._target), ('controlIdentity', self._control)):
            value = record[name]; info = os.fstat(fd)
            if (not isinstance(value, list) or len(value) != 2
                    or any(type(item) is not int or item < 0 for item in value)
                    or value != [info.st_dev, info.st_ino]):
                raise ReservationError('recovery reservation inode differs')
        if (set(intent) != {'schemaVersion', 'state', 'operation'}
                or type(intent['schemaVersion']) is not int or intent['schemaVersion'] != 1
                or intent['state'] != 'pending' or intent['operation'] != 'recovery_reserve'):
            raise ReservationError('recovery reservation intent differs')

    def __enter__(self):
        if self._used: raise ReservationError('recovery reservation context cannot be reused')
        self._used = True
        try:
            parent = self._directory(self.path.anchor, ancestor=True)
            for component in self.path.parent.parts[1:]:
                parent = self._directory(component, parent, ancestor=True)
            info = os.fstat(parent)
            if info.st_uid != os.geteuid() or info.st_mode & 0o022:
                raise ReservationError('recovery reservation parent is unsafe')
            self._target = self._directory(self.path.name, parent)
            self._control = self._directory('.surveyctl', self._target)
            if self._read('operation.lock', 0) != b'':
                raise ReservationError('recovery reservation lock is invalid')
            fcntl.flock(self._files['operation.lock'][0], fcntl.LOCK_EX | fcntl.LOCK_NB)
            record = self._json(self._read('recovery.reservation.json', 8192))
            intent = self._json(self._read('operation.intent', 512))
            self._validate(record, intent)
            self._active = True
            self.inspect()
            return self
        except BaseException as exc:
            self._close()
            if isinstance(exc, OSError):
                raise ReservationError('recovery reservation could not be locked or read safely') from exc
            raise

    def _check_directories(self):
        for parent, name, fd, expected in self._directories:
            observed = os.stat(name, follow_symlinks=False,
                               **({'dir_fd': parent} if parent is not None else {}))
            if _identity(observed) != expected or _identity(os.fstat(fd)) != expected:
                raise ReservationError('recovery reservation directory changed')
        for fd, allowed in ((self._target, {'.surveyctl'}), (self._control, self.FILES)):
            found = set()
            with os.scandir(fd) as entries:
                for entry in entries:
                    if entry.name not in allowed or entry.name in found:
                        raise ReservationError('recovery reservation inventory changed')
                    found.add(entry.name)
            if found != allowed:
                raise ReservationError('recovery reservation inventory changed')

    def inspect(self):
        if not self._active or self._failed:
            raise ReservationError('recovery reservation context is not active or has failed')
        try:
            self._check_directories()
            for name, (fd, expected, digest) in self._files.items():
                if (_snapshot(os.stat(name, dir_fd=self._control, follow_symlinks=False)) != expected
                        or _snapshot(os.fstat(fd)) != expected):
                    raise ReservationError('recovery reservation file changed')
                os.lseek(fd, 0, os.SEEK_SET)
                data = os.read(fd, expected[6] + 1)
                if (hashlib.sha256(data).digest() != digest or _snapshot(os.fstat(fd)) != expected
                        or _snapshot(os.stat(name, dir_fd=self._control, follow_symlinks=False)) != expected):
                    raise ReservationError('recovery reservation contents changed')
            self._check_directories()
            return {'schemaVersion': 1, 'status': 'reservation-verified-not-authorized',
                    'executable': False, 'backupAuthenticated': False, 'runtimeResourcesCreated': False,
                    'reservationId': self.nonce, 'projectProposal': 'survey-recovery-' + self.nonce}
        except OSError as exc:
            self._failed = True
            raise ReservationError('recovery reservation recheck failed') from exc
        except ReservationError:
            self._failed = True
            raise

    def handoff(self, *, receipts):
        """Transfer the same held descriptors to an explicit FS-only phase.

        This creates a persistent, non-authorizing pending marker. It neither
        starts runtime resources nor admits data import. No caller exists in
        the product recovery/CLI path.
        """
        from recovery_handoff import ReservationHandoff
        return ReservationHandoff(self, receipts)

    def _close(self):
        self._active = False
        descriptors, self._fds = self._fds, []
        error = None
        for fd in reversed(descriptors):
            try: os.close(fd)
            except OSError as exc: error = exc
        if error is not None:
            raise ReservationError('recovery reservation descriptor release is unverified') from error

    def __exit__(self, kind, value, traceback):
        try:
            if kind is None and not self._transferred: self.inspect()
        finally:
            self._close()
