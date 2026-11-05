"""Create ephemeral test handoff values without changing a database or issuing a JWT.

This module does not authorize access to any installation. A separate isolated
fixture supervisor must establish target ownership and persist the database row.
"""

import hashlib
import json
import os
import re
import secrets
import stat
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

_OPAQUE = re.compile(r'[A-Za-z0-9_-]{43}')


def _uuid(value):
    if not isinstance(value, str):
        raise ValueError('fixture identity must be a canonical UUID')
    try:
        parsed = uuid.UUID(value)
    except ValueError as exc:
        raise ValueError('fixture identity must be a canonical UUID') from exc
    if str(parsed) != value or parsed.version != 4 or parsed.variant != uuid.RFC_4122:
        raise ValueError('fixture identity must be a canonical random UUID')
    return value


class FixtureHandoff:
    # Avoid exposing the ephemeral browser values through automatic repr/logging.
    def __init__(self, tenant, principal, connection, handoff, nonce, expires):
        self._tenant, self._principal, self._connection = tenant, principal, connection
        self._handoff, self._nonce, self._expires = handoff, nonce, expires

    def database_values(self):
        return {'tenant_id': self._tenant, 'principal_id': self._principal,
                'connection_id': self._connection, 'provider': 'wecom',
                'handoff_hash': hashlib.sha256(self._handoff.encode('ascii')).hexdigest(),
                'browser_hash': hashlib.sha256(self._nonce.encode('ascii')).hexdigest(),
                'expires_at': self._expires}

    def browser_values(self):
        return {'tenantId': self._tenant, 'principalId': self._principal,
                'handoff': self._handoff, 'nonce': self._nonce,
                'expiresAt': self._expires.isoformat().replace('+00:00', 'Z')}


def make_handoff(tenant, principal, connection, now=None):
    tenant, principal, connection = map(_uuid, (tenant, principal, connection))
    now = datetime.now(timezone.utc) if now is None else now
    if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() != timedelta(0):
        raise ValueError('fixture clock must use UTC')
    handoff, nonce = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    if _OPAQUE.fullmatch(handoff) is None or _OPAQUE.fullmatch(nonce) is None or handoff == nonce:
        raise ValueError('fixture randomness is invalid')
    return FixtureHandoff(tenant, principal, connection, handoff, nonce, now + timedelta(seconds=60))


def write_browser_fixture(handoff, directory):
    """Write once into a private same-user test directory; not a target authorization."""
    directory = Path(directory)
    if not isinstance(handoff, FixtureHandoff) or not directory.is_absolute():
        raise ValueError('invalid browser fixture destination')
    directory_fd = None
    created = False
    try:
        directory_fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        metadata = os.fstat(directory_fd)
        if (not stat.S_ISDIR(metadata.st_mode) or stat.S_IMODE(metadata.st_mode) != 0o700 or
                metadata.st_uid != os.geteuid()):
            raise ValueError('unsafe browser fixture directory')
        fd = os.open('handoff.json', os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                     0o600, dir_fd=directory_fd)
        created = True
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump(handoff.browser_values(), stream, sort_keys=True)
    except (OSError, ValueError) as exc:
        if created and directory_fd is not None:
            try:
                os.unlink('handoff.json', dir_fd=directory_fd)
            except OSError:
                pass
        raise ValueError('browser fixture cannot be written') from exc
    finally:
        if directory_fd is not None:
            os.close(directory_fd)
    return directory / 'handoff.json'
