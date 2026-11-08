#!/usr/bin/env python3
"""Compare a candidate's native scripts with the current workflow's trusted tree.

This is an integrity preflight, not authorization to execute a lifecycle.
"""
import gzip
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import stat
import sys
import tarfile
import zlib


FILES = ('native_acceptance.sh', 'native_acceptance_evidence.py')
MAX_SCRIPT = 2 * 1024 * 1024
MAX_IDENTITY_BYTES = 1024 * 1024
MAX_ARCHIVE_BYTES = 128 * 1024 * 1024
MAX_EXPANDED_BYTES = 128 * 1024 * 1024
MAX_ARCHIVE_MEMBERS = 4096


class CandidateRejected(ValueError):
    pass


def read_regular(path: Path, limit: int) -> bytes:
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > limit:
            raise CandidateRejected('unsafe or oversized input')
        payload = stream.read(limit + 1)
        if len(payload) > limit:
            raise CandidateRejected('oversized input')
        return payload


class BoundedReader:
    def __init__(self, source, limit):
        self.source, self.remaining = source, limit

    def read(self, size=-1):
        if size < 0 or size > self.remaining:
            raise CandidateRejected('expanded archive exceeds limit')
        result = self.source.read(size)
        self.remaining -= len(result)
        return result


def verify(identity_path: Path, candidate_dir: Path, architecture: str, trusted_dir: Path) -> None:
    if architecture not in ('amd64', 'arm64'):
        raise CandidateRejected('unsupported architecture')
    try:
        identity = json.loads(read_regular(identity_path, MAX_IDENTITY_BYTES).decode('utf-8'))
        entry = identity['bundles'][architecture]
        name, expected = entry['name'], entry['sha256']
        if (not isinstance(name, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,199}', name)
                or not isinstance(expected, str) or not re.fullmatch(r'[0-9a-f]{64}', expected)):
            raise CandidateRejected('invalid bundle identity')
        trusted = {name: read_regular(trusted_dir / name, MAX_SCRIPT) for name in FILES}
        if any(not value or len(value) > MAX_SCRIPT for value in trusted.values()):
            raise CandidateRejected('trusted script is missing or oversized')
        fd = os.open(candidate_dir / name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, 'rb') as bundle:
            info = os.fstat(bundle.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_ARCHIVE_BYTES:
                raise CandidateRejected('bundle is unsafe or oversized')
            digest = hashlib.sha256()
            compressed = 0
            for chunk in iter(lambda: bundle.read(1024 * 1024), b''):
                compressed += len(chunk)
                if compressed > MAX_ARCHIVE_BYTES:
                    raise CandidateRejected('bundle exceeds compressed limit')
                digest.update(chunk)
            if not hmac.compare_digest(digest.hexdigest(), expected):
                raise CandidateRejected('bundle digest mismatch')
            bundle.seek(0)
            found = set()
            declared = 0
            with gzip.GzipFile(fileobj=bundle) as decompressed, tarfile.open(
                    fileobj=BoundedReader(decompressed, MAX_EXPANDED_BYTES), mode='r|') as archive:
                for index, member in enumerate(archive, start=1):
                    declared += member.size
                    if index > MAX_ARCHIVE_MEMBERS or member.size < 0 or declared > MAX_EXPANDED_BYTES:
                        raise CandidateRejected('archive resource limit exceeded')
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
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, KeyError, TypeError,
            tarfile.TarError, EOFError, zlib.error) as exc:
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
