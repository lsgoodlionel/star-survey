"""Prepare a one-use author fixture for an isolated unprivileged worker.

This only manages a temporary file; it does not authorize a candidate, prove
worker quiescence, or clean a database. Cleanup requires the caller to first
prove the entire worker containment is empty.
"""
import os
from pathlib import Path
import stat
import tempfile

from fixture_handoff import FixtureHandoff, write_browser_fixture


class BrowserFixtureError(RuntimeError):
    pass


class BrowserFixtureFile:
    def __init__(self, directory, fd, uid, identity):
        self.directory, self.fd, self.uid, self.identity = directory, fd, uid, identity

    @classmethod
    def create(cls, parent, handoff, uid, gid):
        if (not isinstance(parent, Path) or not parent.is_absolute() or parent.is_symlink() or
                not isinstance(handoff, FixtureHandoff) or type(uid) is not int or uid <= 0 or
                type(gid) is not int or gid <= 0):
            raise BrowserFixtureError('browser fixture parameters invalid')
        parent_fd = fd = None
        directory = None
        try:
            parent_fd = os.open(parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            info = os.fstat(parent_fd); mode = stat.S_IMODE(info.st_mode)
            private_parent = info.st_uid == os.geteuid() and mode == 0o700
            sticky_parent = info.st_uid == 0 and mode == 0o1777
            if not stat.S_ISDIR(info.st_mode) or not (private_parent or sticky_parent):
                raise BrowserFixtureError('browser fixture parent is unsafe')
            directory = Path(tempfile.mkdtemp(prefix='survey-author-', dir=str(parent)))
            fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            created = os.fstat(fd)
            write_browser_fixture(handoff, directory)
            file_fd = os.open('handoff.json', os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
            try:
                item = os.fstat(file_fd)
                if not stat.S_ISREG(item.st_mode) or item.st_nlink != 1 or stat.S_IMODE(item.st_mode) != 0o600:
                    raise BrowserFixtureError('browser fixture file is unsafe')
                os.fchown(file_fd, uid, gid)
                os.fsync(file_fd)
            finally:
                os.close(file_fd)
            os.fchown(fd, uid, gid); os.fsync(fd)
            return cls(directory, fd, uid, (created.st_dev, created.st_ino))
        except BaseException:
            if fd is not None:
                try: os.unlink('handoff.json', dir_fd=fd)
                except OSError: pass
                os.close(fd)
            if directory is not None:
                try: os.rmdir(directory)
                except OSError: pass
            raise BrowserFixtureError('browser fixture could not be prepared') from None
        finally:
            if parent_fd is not None: os.close(parent_fd)

    def cleanup(self):
        """Remove only the bound private directory after worker containment is empty."""
        try:
            current = os.stat(self.directory, follow_symlinks=False)
            held = os.fstat(self.fd)
            if (not stat.S_ISDIR(current.st_mode) or (current.st_dev, current.st_ino) != self.identity or
                    (held.st_dev, held.st_ino) != self.identity or current.st_uid != self.uid):
                raise BrowserFixtureError('browser fixture directory changed')
            entries = os.listdir(self.fd)
            if any(name != 'handoff.json' for name in entries):
                raise BrowserFixtureError('browser fixture directory has unknown entries')
            if entries:
                fd = os.open('handoff.json', os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=self.fd)
                try:
                    item = os.fstat(fd)
                    if (not stat.S_ISREG(item.st_mode) or item.st_uid != self.uid or
                            item.st_nlink != 1 or stat.S_IMODE(item.st_mode) != 0o600):
                        raise BrowserFixtureError('browser fixture file changed')
                finally:
                    os.close(fd)
                os.unlink('handoff.json', dir_fd=self.fd)
            os.rmdir(self.directory)
            os.close(self.fd); self.fd = None
        except (OSError, TypeError, ValueError):
            raise BrowserFixtureError('browser fixture cleanup unverified') from None
