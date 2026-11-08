#!/usr/bin/env python3
"""Compare a candidate's native scripts with the current workflow's trusted tree.

This is an integrity preflight, not authorization to execute a lifecycle.
"""
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import stat
import sys
import tarfile


FILES = ('native_acceptance.sh', 'native_acceptance_evidence.py')
MAX_SCRIPT = 2 * 1024 * 1024


class CandidateRejected(ValueError):
    pass


def verify(identity_path: Path, candidate_dir: Path, architecture: str, trusted_dir: Path) -> None:
    if architecture not in ('amd64', 'arm64'):
        raise CandidateRejected('unsupported architecture')
    try:
        identity = json.loads(identity_path.read_text(encoding='utf-8'))
        entry = identity['bundles'][architecture]
        name, expected = entry['name'], entry['sha256']
        if (not isinstance(name, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,199}', name)
                or not isinstance(expected, str) or not re.fullmatch(r'[0-9a-f]{64}', expected)):
            raise CandidateRejected('invalid bundle identity')
        trusted = {name: (trusted_dir / name).read_bytes() for name in FILES}
        if any(not value or len(value) > MAX_SCRIPT for value in trusted.values()):
            raise CandidateRejected('trusted script is missing or oversized')
        fd = os.open(candidate_dir / name, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(fd, 'rb') as bundle:
            if not stat.S_ISREG(os.fstat(bundle.fileno()).st_mode):
                raise CandidateRejected('bundle is not a regular file')
            digest = hashlib.sha256()
            for chunk in iter(lambda: bundle.read(1024 * 1024), b''):
                digest.update(chunk)
            if not hmac.compare_digest(digest.hexdigest(), expected):
                raise CandidateRejected('bundle digest mismatch')
            bundle.seek(0)
            found = set()
            with tarfile.open(fileobj=bundle, mode='r:gz') as archive:
                for member in archive:
                    if member.name not in {'release/' + item for item in FILES}:
                        continue
                    item = member.name.removeprefix('release/')
                    if item in found or not member.isfile() or member.size > MAX_SCRIPT:
                        raise CandidateRejected('unsafe or duplicate script member')
                    stream = archive.extractfile(member)
                    if stream is None or not hmac.compare_digest(stream.read(MAX_SCRIPT + 1), trusted[item]):
                        raise CandidateRejected('candidate script differs from trusted workflow tree')
                    found.add(item)
            if found != set(FILES):
                raise CandidateRejected('candidate script missing')
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, KeyError, TypeError, tarfile.TarError) as exc:
        raise CandidateRejected('candidate proof is missing or invalid') from exc


def main() -> int:
    if len(sys.argv) != 5:
        return 2
    try:
        verify(Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3], Path(sys.argv[4]))
    except CandidateRejected:
        print('native candidate integrity proof rejected', file=sys.stderr)
        return 1
    print('native candidate scripts match trusted workflow tree; lifecycle remains blocked')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
