"""Private pending marker for a future isolated fixture supervisor.

It records intent, not ownership or proof of recovery. A marker on an ephemeral
runner may disappear with the VM; external workflow evidence must still treat an
interrupted job as unverified. Same-user code can alter or forge this marker.
"""

import os
import re
import stat
from pathlib import Path

_NAME = 'fixture-recovery.pending'


class RecoveryMarker:
    def __init__(self, directory, identity):
        if (not isinstance(directory, Path) or not directory.is_absolute() or
                not isinstance(identity, str) or re.fullmatch(r'[0-9a-f]{64}', identity) is None):
            raise ValueError('invalid recovery marker')
        self.directory = directory
        self.identity = identity.encode('ascii') + b'\n'

    def _open_directory(self):
        fd = os.open(self.directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        info = os.fstat(fd)
        if not stat.S_ISDIR(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o700 or info.st_uid != os.geteuid():
            os.close(fd)
            raise ValueError('unsafe recovery marker directory')
        return fd

    def create(self):
        directory = None
        try:
            directory = self._open_directory()
            fd = os.open(_NAME, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=directory)
            with os.fdopen(fd, 'wb') as stream:
                stream.write(self.identity)
                stream.flush()
                os.fsync(stream.fileno())
            os.fsync(directory)
        except OSError:
            # An existing or partially written marker must remain for inspection.
            raise ValueError('cannot create recovery marker') from None
        finally:
            if directory is not None:
                os.close(directory)

    def clear(self):
        directory = None
        try:
            directory = self._open_directory()
            fd = os.open(_NAME, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
            with os.fdopen(fd, 'rb') as stream:
                info = os.fstat(stream.fileno())
                if (not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o600 or
                        info.st_uid != os.geteuid() or info.st_nlink != 1 or
                        stream.read(66) != self.identity):
                    raise ValueError('recovery marker differs')
            os.unlink(_NAME, dir_fd=directory)
            os.fsync(directory)
        except OSError:
            raise ValueError('cannot clear recovery marker') from None
        finally:
            if directory is not None:
                os.close(directory)
