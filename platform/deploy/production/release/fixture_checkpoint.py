"""Test-only pending checkpoint, separate from a disposable candidate work tree.

A local checkpoint records an unresolved operation; it does not establish
ownership, exclusivity, or recovery. Runner loss may destroy it before artifact
upload, so externally an interrupted job must remain unverified. Same-UID code
can forge it; callers must independently constrain workers and verify outcomes.
"""
import os
import stat
from pathlib import Path
from fixture_recovery_marker import RecoveryMarker


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
    marker.create()
    action()
    if verify() is not True:
        raise RuntimeError('recovery verification failed; checkpoint retained')
    marker.clear()
