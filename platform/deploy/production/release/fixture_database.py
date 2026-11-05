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
        if manifest['version'] != version:
            raise ValueError('fixture candidate version differs')
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
    return release, containers[0]['Id']


def validate_database_container(container, release, expected_image):
    if (not isinstance(container, dict) or not isinstance(release, str) or not release.startswith('/') or
            not isinstance(expected_image, str) or
            re.fullmatch(r'[a-z0-9][a-z0-9./:_-]*@sha256:[0-9a-f]{64}', expected_image) is None):
        raise ValueError('invalid isolated database container')
    if not isinstance(container.get('Id'), str) or re.fullmatch(r'[0-9a-f]{64}', container['Id']) is None:
        raise ValueError('isolated database ID is invalid')
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

    # Compose's documented database contract. Do not infer data location from
    # the volume label alone: env, startup arguments and all mounts matter.
    if config.get('Entrypoint') != ['docker-entrypoint.sh'] or config.get('Cmd') != ['postgres']:
        raise ValueError('isolated database startup is invalid')
    entries = config.get('Env')
    if not isinstance(entries, list) or any(not isinstance(item, str) or '=' not in item for item in entries):
        raise ValueError('isolated database environment is invalid')
    env = {}
    for item in entries:
        key, value = item.split('=', 1)
        if key in env:
            raise ValueError('isolated database environment is ambiguous')
        env[key] = value
    expected_env = {
        'PGDATA': '/var/lib/postgresql/data', 'POSTGRES_USER': 'postgres', 'POSTGRES_DB': 'postgres',
        'POSTGRES_PASSWORD_FILE': '/run/secrets/platform_db_superuser_password',
        'PLATFORM_DB_OWNER_PASSWORD_FILE': '/run/secrets/platform_db_owner_password',
        'PLATFORM_DB_APP_PASSWORD_FILE': '/run/secrets/platform_db_app_password',
    }
    if any(env.get(key) != value for key, value in expected_env.items()):
        raise ValueError('isolated database environment differs')
    allowed_pg = set(expected_env) | {'PG_MAJOR', 'PG_VERSION', 'PG_SHA256', 'PG_CONFIGURE_ARGS'}
    if any((key.startswith('PG') or key.startswith('POSTGRES_') or key.startswith('PLATFORM_DB_'))
           and key not in allowed_pg for key in env):
        raise ValueError('unexpected database environment')
    target = Path(release).parent.parent.parent
    binds = {'/docker-entrypoint-initdb.d/10-platform-roles.sh': release + '/init/init-platform-db.sh'}
    for name in ('platform_db_superuser_password', 'platform_db_owner_password', 'platform_db_app_password'):
        binds['/run/secrets/' + name] = str(target / 'shared/runtime-secrets' / name)
    if len(mounts) != 1 + len(binds):
        raise ValueError('isolated database mount set differs')
    seen = set()
    for mount in mounts:
        if mount is volumes[0]:
            continue
        destination = mount.get('Destination')
        if (destination not in binds or destination in seen or mount.get('Type') != 'bind' or
                mount.get('Source') != binds[destination] or mount.get('RW') is not False):
            raise ValueError('isolated database bind is invalid')
        seen.add(destination)
    if seen != set(binds):
        raise ValueError('isolated database bind set is incomplete')


def revalidate_fixture_database(work, marker, expected_release, expected_container_id):
    """Reinspect immediately before a future operation; this is not an ownership proof.

    Callers must address Docker by the returned full ID, never by service name.
    A check and a subsequent operation are not atomic with container replacement.
    """
    if not isinstance(expected_container_id, str) or re.fullmatch(r'[0-9a-f]{64}', expected_container_id) is None:
        raise ValueError('fixture database ID is invalid')
    release, container_id = inspect_fixture_database(work, marker)
    if release != expected_release or container_id != expected_container_id:
        raise ValueError('fixture database changed')
    return container_id
