"""Test-only pending checkpoint, separate from a disposable candidate work tree.

A local checkpoint records an unresolved operation; it does not establish
ownership, exclusivity, or recovery. Runner loss may destroy it before artifact
upload, so externally an interrupted job must remain unverified. Same-UID code
can forge it; callers must independently constrain workers and verify outcomes.
"""
import os
import stat
import sys
from pathlib import Path
from fixture_recovery_marker import RecoveryMarker

_RECEIPT = 'fixture-recovery.verified'


def _directory(path):
    if not isinstance(path, Path) or not path.is_absolute():
        raise ValueError('checkpoint path is invalid')
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        info = os.fstat(fd)
        if not stat.S_ISDIR(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o700 or info.st_uid != os.geteuid():
            raise ValueError('checkpoint directory is unsafe')
    finally:
        os.close(fd)
    return path.resolve(strict=True)


def run_checkpointed(evidence, work, identity, action, verify):
    """Create intent before action; clear it only after an explicit verified result.

    The action and verifier are trusted supervisor code, not a proof supplied by
    an untrusted worker. Failure, cancellation and ambiguous results retain the
    marker. Host loss cannot be distinguished from incomplete upload locally.
    """
    if not callable(action) or not callable(verify):
        raise ValueError('checkpoint callbacks are invalid')
    evidence_path, work_path = _directory(evidence), _directory(work)
    if evidence_path == work_path or work_path in evidence_path.parents:
        raise ValueError('checkpoint must be outside candidate work')
    marker = RecoveryMarker(evidence_path, identity)
    receipt = evidence_path / _RECEIPT
    if os.path.lexists(receipt):
        raise ValueError('checkpoint receipt already exists')
    marker.create()
    action()
    if verify() is not True:
        raise RuntimeError('recovery verification failed; checkpoint retained')
    directory = os.open(evidence_path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        fd = os.open(_RECEIPT, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=directory)
        with os.fdopen(fd, 'wb') as stream:
            stream.write(identity.encode('ascii') + b'\n')
            stream.flush()
            os.fsync(stream.fileno())
        os.fsync(directory)
    finally:
        os.close(directory)
    marker.clear()


def require_completed_checkpoint(evidence, work, identity):
    """Require a matching verified receipt and no pending marker, fail closed."""
    evidence_path, work_path = _directory(evidence), _directory(work)
    if evidence_path == work_path or work_path in evidence_path.parents:
        raise ValueError('checkpoint must be outside candidate work')
    marker = RecoveryMarker(evidence_path, identity)
    if os.path.lexists(evidence_path / 'fixture-recovery.pending'):
        raise RuntimeError('recovery remains pending')
    directory = os.open(evidence_path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        fd = os.open(_RECEIPT, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
        with os.fdopen(fd, 'rb') as stream:
            info = os.fstat(stream.fileno())
            if (not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o600 or
                    info.st_uid != os.geteuid() or info.st_nlink != 1 or stream.read(66) != marker.identity):
                raise ValueError('checkpoint receipt differs')
    finally:
        os.close(directory)


if __name__ == '__main__':
    if len(sys.argv) != 5 or sys.argv[1] != 'verify':
        raise SystemExit('invalid checkpoint command')
    require_completed_checkpoint(Path(sys.argv[2]), Path(sys.argv[3]), sys.argv[4])
