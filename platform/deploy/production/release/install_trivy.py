"""Install the policy-pinned Linux amd64 scanner from its official release.

The checksum comes from the same GitHub release used by the upstream installer;
this does not introduce a new publisher or change the vulnerability policy.
"""
import argparse
import hashlib
import io
import os
from pathlib import Path
import re
import tarfile
import tempfile
from urllib.request import urlopen


def download(url):
    with urlopen(url, timeout=120) as response:
        if not response.geturl().startswith('https://'):
            raise ValueError('download must remain HTTPS')
        return response.read(128 * 1024 * 1024 + 1)


def install(version, destination):
    if not re.fullmatch(r'v\d+\.\d+\.\d+', version):
        raise ValueError('invalid pinned scanner version')
    name = f'trivy_{version[1:]}_Linux-64bit.tar.gz'
    base = f'https://github.com/aquasecurity/trivy/releases/download/{version}/'
    archive = download(base + name)
    if len(archive) > 128 * 1024 * 1024:
        raise ValueError('scanner archive exceeds limit')
    checksums = download(base + f'trivy_{version[1:]}_checksums.txt')
    if len(checksums) > 1024 * 1024:
        raise ValueError('checksum document exceeds limit')
    matches = []
    for line in checksums.decode('ascii').splitlines():
        match = re.fullmatch(r'([0-9a-f]{64})  (\S+)', line)
        if not match:
            raise ValueError('invalid checksum record')
        if match[2] == name:
            matches.append(match[1])
    if len(matches) != 1 or hashlib.sha256(archive).hexdigest() != matches[0]:
        raise ValueError('scanner checksum mismatch or missing unique record')
    with tarfile.open(fileobj=io.BytesIO(archive), mode='r:gz') as package:
        binaries = [entry for entry in package.getmembers() if entry.name == 'trivy']
        if len(binaries) != 1 or not binaries[0].isreg() or not 0 < binaries[0].size <= 256 * 1024 * 1024:
            raise ValueError('scanner archive must contain one regular binary')
        # Never extract archive paths, symlinks, permissions, or other members.
        binary = package.extractfile(binaries[0]).read()
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(dir=destination, prefix='.trivy-')
    try:
        with os.fdopen(descriptor, 'wb') as stream:
            stream.write(binary)
            stream.flush()
            os.fsync(stream.fileno())
            os.fchmod(stream.fileno(), 0o755)
        os.replace(temporary, destination / 'trivy')
    finally:
        Path(temporary).unlink(missing_ok=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--version', required=True)
    parser.add_argument('--destination', type=Path, required=True)
    args = parser.parse_args()
    install(args.version, args.destination)
