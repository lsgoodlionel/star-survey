"""Test-only validation for the candidate's isolated fixture database.

This module does not authorize purge or create a database identity. Validation
must be combined with the hosted-runner preflight and the candidate marker.
"""

import hashlib
import json
import os
import re
import stat
import subprocess
from pathlib import Path

from native_acceptance_evidence import verify_fixture_owner, verify_fixture_volume_snapshot


def _read_regular(path, limit):
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, 'rb') as stream:
            metadata = os.fstat(stream.fileno())
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != os.geteuid() or metadata.st_size > limit:
                raise ValueError('fixture file is unsafe')
            payload = stream.read(limit + 1)
            if len(payload) > limit:
                raise ValueError('fixture file is oversized')
            return payload
    except OSError as exc:
        raise ValueError('fixture file cannot be read') from exc


def inspect_fixture_database(work, marker):
    """Inspect only the current candidate database after independent fixture checks."""
    target = work / 'primary'
    verify_fixture_owner(work, target, marker)
    if os.geteuid() != 0:
        raise ValueError('fixture database inspection requires root')
    verify_fixture_volume_snapshot(work, marker, work / 'volumes.json')
    for directory in (target, target / 'releases'):
        if directory.is_symlink() or not directory.is_dir():
            raise ValueError('fixture release directory is unsafe')
    try:
        version = _read_regular(target / 'current', 128).decode('ascii').strip()
    except UnicodeDecodeError as exc:
        raise ValueError('fixture release version is invalid') from exc
    if re.fullmatch(r'[0-9]+\.[0-9]+\.[0-9]+(?:-rc\.[0-9]+)?', version) is None:
        raise ValueError('fixture release version is invalid')
    release = target / 'releases' / version
    if release.is_symlink() or not release.is_dir():
        raise ValueError('fixture release directory is unsafe')
    payload = _read_regular(release / 'release.json', 1024 * 1024)
    if hashlib.sha256(payload).hexdigest() != os.environ.get('CANDIDATE_MANIFEST_SHA256'):
        raise ValueError('fixture candidate manifest differs')
    try:
        manifest = json.loads(payload)
        image = manifest['images']['POSTGRES_IMAGE']
        output = subprocess.run(['docker', '--host', 'unix:///var/run/docker.sock', 'inspect', '--type',
                                 'container', 'survey-production-platform-db-1'],
                                check=True, capture_output=True, text=True, timeout=30)
        containers = json.loads(output.stdout)
    except (OSError, subprocess.SubprocessError, UnicodeDecodeError, json.JSONDecodeError, KeyError, TypeError) as exc:
        raise ValueError('fixture database inspection failed') from exc
    if not isinstance(containers, list) or len(containers) != 1:
        raise ValueError('fixture database inspection is ambiguous')
    validate_database_container(containers[0], str(release), image)
    return release


def validate_database_container(container, release, expected_image):
    if (not isinstance(container, dict) or not isinstance(release, str) or not release.startswith('/') or
            not isinstance(expected_image, str) or
            re.fullmatch(r'[a-z0-9][a-z0-9./:_-]*@sha256:[0-9a-f]{64}', expected_image) is None):
        raise ValueError('invalid isolated database container')
    state = container.get('State')
    config = container.get('Config')
    mounts = container.get('Mounts')
    if (not isinstance(state, dict) or state.get('Running') is not True or not isinstance(config, dict) or
            config.get('Image') != expected_image):
        raise ValueError('isolated database container is not running')
    labels = config.get('Labels')
    if not isinstance(labels, dict) or any(labels.get(key) != expected for key, expected in {
        'com.docker.compose.project': 'survey-production',
        'com.docker.compose.service': 'platform-db',
        'com.docker.compose.project.working_dir': release,
        'com.docker.compose.project.config_files': release + '/compose.yml',
    }.items()):
        raise ValueError('isolated database container binding is invalid')
    if not isinstance(mounts, list) or any(not isinstance(mount, dict) for mount in mounts):
        raise ValueError('isolated database mounts are invalid')
    volumes = [mount for mount in mounts if mount.get('Type') == 'volume']
    if (len(volumes) != 1 or volumes[0].get('Name') != 'survey-production_platform-db-data' or
            volumes[0].get('Destination') != '/var/lib/postgresql/data'):
        raise ValueError('isolated database volume binding is invalid')
