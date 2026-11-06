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


def require_unused_worker_ids(uid, gid):
    """Check this hosted test host for an already-used numeric worker identity.

    This snapshot assumes a trusted host and cannot defeat a same-privilege
    process started after the check. No persistent user or group is created.
    """
    if (os.geteuid() != 0 or type(uid) is not int or type(gid) is not int or
            not 60000 <= uid < 2**31 or not 60000 <= gid < 2**31):
        raise BrowserFixtureError('browser worker identity invalid')
    try:
        for entry in Path('/proc').iterdir():
            if not entry.name.isdigit(): continue
            try:
                lines = (entry / 'status').read_text().splitlines()
            except FileNotFoundError:
                continue
            fields = {line.split(':', 1)[0]: line.split(':', 1)[1].split()
                      for line in lines if ':' in line}
            if not all(key in fields for key in ('Uid', 'Gid', 'Groups')):
                raise BrowserFixtureError('process identity unavailable')
            if uid in map(int, fields['Uid']) or gid in map(int, fields['Gid'] + fields['Groups']):
                raise BrowserFixtureError('browser worker identity already in use')
    except (OSError, ValueError):
        raise BrowserFixtureError('browser worker identity unverified') from None


class BrowserFixtureFile:
    def __init__(self, directory, fd, gid, identity):
        self.directory, self.fd, self.gid, self.identity = directory, fd, gid, identity

    @classmethod
    def create(cls, parent, handoff, uid, gid):
        if (os.geteuid() != 0 or not isinstance(parent, Path) or not parent.is_absolute() or parent.is_symlink() or
                not isinstance(handoff, FixtureHandoff) or type(uid) is not int or uid <= 0 or
                type(gid) is not int or gid <= 0):
            raise BrowserFixtureError('browser fixture parameters invalid')
        parent_fd = fd = None
        directory = None
        try:
            require_unused_worker_ids(uid, gid)
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
                os.fchown(file_fd, 0, gid); os.fchmod(file_fd, 0o440)
                os.fsync(file_fd)
            finally:
                os.close(file_fd)
            os.fchown(fd, 0, gid); os.fchmod(fd, 0o710); os.fsync(fd)
            return cls(directory, fd, gid, (created.st_dev, created.st_ino))
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
                    (held.st_dev, held.st_ino) != self.identity or current.st_uid != 0 or
                    current.st_gid != self.gid or stat.S_IMODE(current.st_mode) != 0o710):
                raise BrowserFixtureError('browser fixture directory changed')
            entries = os.listdir(self.fd)
            if any(name != 'handoff.json' for name in entries):
                raise BrowserFixtureError('browser fixture directory has unknown entries')
            if entries:
                fd = os.open('handoff.json', os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=self.fd)
                try:
                    item = os.fstat(fd)
                    if (not stat.S_ISREG(item.st_mode) or item.st_uid != 0 or item.st_gid != self.gid or
                            item.st_nlink != 1 or stat.S_IMODE(item.st_mode) != 0o440):
                        raise BrowserFixtureError('browser fixture file changed')
                finally:
                    os.close(fd)
                os.unlink('handoff.json', dir_fd=self.fd)
            os.rmdir(self.directory)
            os.close(self.fd); self.fd = None
        except (OSError, TypeError, ValueError):
            raise BrowserFixtureError('browser fixture cleanup unverified') from None
