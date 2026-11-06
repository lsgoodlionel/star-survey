"""Supervised PostgreSQL session for isolated fixture experiments only.

This is not target authorization, an exclusive lease, or a recovery mechanism.
The trusted caller must prove a disposable runner/instance, fixed container and
network identities, daemon control and absence of other writers. A disconnected
session or failed binding is an unverified outcome; never reconnect blindly.
"""
import json
import os
import re
import secrets
import select
import subprocess
import time

_DOCKER = ['docker', '--host', 'unix:///var/run/docker.sock']
# Secrets remain inside the test database container and never enter host argv.
_COMMAND = ('export PGPASSWORD="$(cat /run/secrets/platform_db_superuser_password)"; '
            'exec psql -X -q -A -t -h 127.0.0.1 -U postgres -d "$1" '
            '-v ON_ERROR_STOP=1 -v VERBOSITY=sqlstate')


class PinnedSessionError(RuntimeError):
    pass


class PinnedDatabaseSession:
    """Hold one superuser connection and bind PID, backend start and DB OID."""
    def __init__(self, container_id, database, verify_binding):
        if (os.geteuid() != 0 or os.environ.get('GITHUB_ACTIONS') != 'true' or
                os.environ.get('RUNNER_ENVIRONMENT') != 'github-hosted' or
                os.environ.get('GITHUB_REPOSITORY') != 'lsgoodlionel/star-survey'):
            raise PinnedSessionError('dedicated hosted runner required')
        if (not isinstance(container_id, str) or re.fullmatch(r'[0-9a-f]{64}', container_id) is None or
                database not in ('postgres', 'platform') or not callable(verify_binding)):
            raise PinnedSessionError('database session parameters invalid')
        self.container_id, self.database, self.verify_binding = container_id, database, verify_binding
        self.process = None
        self.poisoned = False
        self.buffer = b''
        self.identity = None
        self.marker = 'FIXTURE_' + secrets.token_hex(16)
        try:
            self._binding()
            self.process = subprocess.Popen(_DOCKER + ['exec', '-i', container_id, 'sh', '-c', _COMMAND, 'fixture', database],
                                            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
            self.identity = self._identity()
        except BaseException:
            self.close()
            raise PinnedSessionError('database session could not be established') from None

    def _binding(self):
        try:
            if self.verify_binding(self.container_id) is not True:
                raise ValueError('binding rejected')
        except BaseException:
            raise PinnedSessionError('database binding unverified') from None

    def _query(self, sql, timeout=15):
        if not isinstance(sql, str) or not sql or '\\' in sql or len(sql) > 2_000_000:
            raise PinnedSessionError('database statement invalid')
        if self.process is None or self.process.poll() is not None:
            raise PinnedSessionError('database session disconnected; outcome unverified')
        try:
            self.process.stdin.write((sql + '\n\\echo ' + self.marker + '\n').encode())
            self.process.stdin.flush()
            marker = (self.marker + '\n').encode()
            deadline = time.monotonic() + timeout
            while marker not in self.buffer:
                remain = deadline - time.monotonic()
                if remain <= 0 or not select.select([self.process.stdout], [], [], remain)[0]:
                    raise PinnedSessionError('database session timed out; outcome unverified')
                chunk = os.read(self.process.stdout.fileno(), 4096)
                if not chunk: raise PinnedSessionError('database session disconnected; outcome unverified')
                self.buffer += chunk
                if len(self.buffer) > 65536: raise PinnedSessionError('database response too large')
            result, self.buffer = self.buffer.split(marker, 1)
            return result.decode('utf-8').strip()
        except (OSError, UnicodeError, ValueError):
            raise PinnedSessionError('database session failed; outcome unverified') from None

    def _identity(self):
        raw = self._query("SELECT json_build_object('pid',a.pid,'started',a.backend_start::text,"
                          "'database_oid',a.datid::bigint,'database',current_database()) "
                          "FROM pg_stat_activity a WHERE a.pid=pg_backend_pid();")
        try:
            data = json.loads(raw)
            if (set(data) != {'pid', 'started', 'database_oid', 'database'} or
                    type(data['pid']) is not int or data['pid'] <= 0 or
                    type(data['database_oid']) is not int or data['database_oid'] <= 0 or
                    not isinstance(data['started'], str) or not data['started'] or data['database'] != self.database):
                raise ValueError('identity invalid')
            return data
        except (ValueError, TypeError, json.JSONDecodeError):
            raise PinnedSessionError('database identity invalid') from None

    def execute(self, sql):
        """Execute trusted supervisor SQL on the pinned connection; never auto-reconnect."""
        if self.poisoned:
            raise PinnedSessionError('database session outcome remains unverified')
        try:
            self._binding()
            if self._identity() != self.identity:
                raise PinnedSessionError('database session identity changed')
            return self._query(sql)
        except BaseException:
            self.poisoned = True
            try:
                self.close()
            except BaseException:
                pass
            raise PinnedSessionError('database session outcome remains unverified') from None

    def close(self):
        if self.process is not None and self.process.poll() is None:
            try:
                self.process.stdin.close()
                self.process.wait(timeout=5)
            except (OSError, subprocess.SubprocessError):
                self.process.kill()
                self.process.wait(timeout=5)
