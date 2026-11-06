"""Test-only pending checkpoint, separate from a disposable candidate work tree.

A local checkpoint records an unresolved operation; it does not establish
ownership, exclusivity, or recovery. Runner loss may destroy it before artifact
upload, so externally an interrupted job must remain unverified. Same-UID code
can forge it; callers must independently constrain workers and verify outcomes.
"""
import hashlib
import json
import os
import re
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


def bound_checkpoint_identity(work, run_id, attempt, manifest_sha):
    """Bind the checkpoint to this run, candidate manifest and work inode."""
    if (not isinstance(run_id, str) or re.fullmatch(r'[1-9][0-9]*', run_id) is None or
            not isinstance(attempt, str) or re.fullmatch(r'[1-9][0-9]*', attempt) is None or
            not isinstance(manifest_sha, str) or re.fullmatch(r'[0-9a-f]{64}', manifest_sha) is None):
        raise ValueError('checkpoint run or candidate identity invalid')
    work_path = _directory(work)
    info = os.stat(work_path, follow_symlinks=False)
    payload = {'run': run_id, 'attempt': attempt, 'manifest': manifest_sha,
               'work': str(work_path), 'device': info.st_dev, 'inode': info.st_ino}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def run_checkpointed(evidence, work, identity, action, verify, *, run_id, attempt, manifest_sha,
                     continue_finalization=lambda: True):
    """Create intent before action; clear it only after an explicit verified result.

    The action and verifier are trusted supervisor code, not a proof supplied by
    an untrusted worker. Failure, cancellation and ambiguous results retain the
    marker. Host loss cannot be distinguished from incomplete upload locally.
    """
    if not callable(action) or not callable(verify) or not callable(continue_finalization):
        raise ValueError('checkpoint callbacks are invalid')
    evidence_path, work_path = _directory(evidence), _directory(work)
    if evidence_path == work_path or work_path in evidence_path.parents:
        raise ValueError('checkpoint must be outside candidate work')
    if identity != bound_checkpoint_identity(work_path, run_id, attempt, manifest_sha):
        raise ValueError('checkpoint binding differs')
    marker = RecoveryMarker(evidence_path, identity)
    receipt = evidence_path / _RECEIPT
    if os.path.lexists(receipt):
        raise ValueError('checkpoint receipt already exists')
    marker.create()
    action()
    if verify() is not True or continue_finalization() is not True:
        raise RuntimeError('recovery verification failed; checkpoint retained')
    try:
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
        if continue_finalization() is not True:
            raise RuntimeError('checkpoint finalization interrupted')
        marker.clear()
        if continue_finalization() is not True:
            raise RuntimeError('checkpoint finalization interrupted')
    except BaseException:
        # unlink may have succeeded even when the following fsync failed.
        # Recreate pending and invalidate the receipt where possible; callers
        # must also require the live supervisor's successful return.
        if not os.path.lexists(evidence_path / 'fixture-recovery.pending'):
            try:
                marker.create()
            except BaseException:
                pass
        try:
            os.unlink(receipt)
        except OSError:
            pass
        raise


def require_completed_checkpoint(evidence, work, identity, *, run_id, attempt, manifest_sha):
    """Require a matching verified receipt and no pending marker, fail closed."""
    evidence_path, work_path = _directory(evidence), _directory(work)
    if evidence_path == work_path or work_path in evidence_path.parents:
        raise ValueError('checkpoint must be outside candidate work')
    if identity != bound_checkpoint_identity(work_path, run_id, attempt, manifest_sha):
        raise ValueError('checkpoint binding differs')
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
    if len(sys.argv) == 6 and sys.argv[1] == 'identity':
        print(bound_checkpoint_identity(Path(sys.argv[2]), sys.argv[3], sys.argv[4], sys.argv[5]))
    elif len(sys.argv) == 8 and sys.argv[1] == 'verify':
        if sys.argv[4] != bound_checkpoint_identity(Path(sys.argv[3]), sys.argv[5], sys.argv[6], sys.argv[7]):
            raise SystemExit('checkpoint binding differs')
        require_completed_checkpoint(Path(sys.argv[2]), Path(sys.argv[3]), sys.argv[4],
                                     run_id=sys.argv[5], attempt=sys.argv[6], manifest_sha=sys.argv[7])
    else:
        raise SystemExit('invalid checkpoint command')
