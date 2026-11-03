#!/usr/bin/env python3
"""Real PostgreSQL component test; confers no backup restoration eligibility."""
import argparse
import json
from pathlib import Path
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
import time

PRODUCTION = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PRODUCTION))
from restore import RestoreManager, RestoreRuntimeError


class PostgresFixtureComplete(Exception):
    pass


def command(args, **kwargs):
    kwargs.setdefault('capture_output', True)
    kwargs.setdefault('timeout', 45)
    return subprocess.run(args, **kwargs)


def verify(image):
    work = Path(tempfile.mkdtemp(prefix='survey-restore-owner-')).resolve()
    work.chmod(0o700)
    containers = []
    creation_uncertain = False
    ownership_label = 'survey.restore-owner-fixture=' + secrets.token_hex(16)
    result = {'schemaVersion': 1, 'scope': 'postgres-restore-owner-component',
              'status': 'failed', 'cleanupPassed': False, 'image': image,
              'restorationEligible': False, 'releaseAcceptance': False}

    def sql(container, statement):
        return command(['docker', 'exec', '-i', container, 'psql', '-v', 'ON_ERROR_STOP=1',
                        '-U', 'postgres', '-d', 'platform', '-qAt'], input=statement.encode())

    try:
        if command(['docker', 'image', 'inspect', image]).returncode != 0:
            if command(['docker', 'pull', image], timeout=180).returncode != 0:
                raise RuntimeError('fixture image unavailable')
        for name in ('super', 'owner', 'app'):
            path = work / name
            path.write_text(secrets.token_hex(32))
            path.chmod(0o600)
        for _ in range(2):
            name = 'survey-owner-' + secrets.token_hex(8)
            collision = command(['docker', 'ps', '-aq', '--filter', 'name=^/' + name + '$'])
            if collision.returncode or collision.stdout.strip():
                raise RuntimeError('fixture collision query failed')
            try:
                created = command([
                    'docker', 'create', '--name', name, '--label', ownership_label, '--network', 'none',
                    '-e', 'POSTGRES_USER=postgres', '-e', 'POSTGRES_DB=postgres',
                    '-e', 'POSTGRES_PASSWORD_FILE=/fixture/super',
                    '-e', 'PLATFORM_DB_OWNER_PASSWORD_FILE=/fixture/owner',
                    '-e', 'PLATFORM_DB_APP_PASSWORD_FILE=/fixture/app',
                    '-v', str(work) + ':/fixture:ro',
                    '-v', str(PRODUCTION / 'init/init-platform-db.sh') +
                    ':/docker-entrypoint-initdb.d/10-platform-roles.sh:ro', image,
                ])
            except subprocess.SubprocessError:
                creation_uncertain = True
                raise
            identifier = created.stdout.decode().strip()
            if created.returncode or re.fullmatch('[0-9a-f]{64}', identifier) is None:
                creation_uncertain = True
                raise RuntimeError('fixture creation failed')
            containers.append(identifier)
            if command(['docker', 'start', identifier]).returncode:
                raise RuntimeError('fixture startup failed')
            deadline = time.monotonic() + 90
            while True:
                # The initialization server accepts only Unix sockets. TCP
                # proves the final server is running before we create data.
                ready = command(['docker', 'exec', identifier, 'pg_isready', '-h', '127.0.0.1',
                                 '-U', 'postgres', '-d', 'platform'])
                if ready.returncode == 0 and sql(identifier, 'SELECT 1;').returncode == 0:
                    break
                if time.monotonic() >= deadline:
                    raise RuntimeError('fixture readiness failed')
                time.sleep(.5)
        source, target = containers
        seed = sql(source, """
SET ROLE platform_owner;
CREATE TABLE flyway_schema_history(installed_rank integer);
INSERT INTO flyway_schema_history VALUES(1);
CREATE FUNCTION fixture_definer() RETURNS name LANGUAGE sql SECURITY DEFINER AS 'SELECT current_user';
GRANT EXECUTE ON FUNCTION fixture_definer() TO platform_app;
""")
        if seed.returncode:
            raise RuntimeError('fixture schema failed')
        dumped = command(['docker', 'exec', source, 'pg_dump', '-U', 'postgres',
                          '--format=custom', '--serializable-deferrable', 'platform'])
        if dumped.returncode:
            raise RuntimeError('fixture dump failed')
        dump = work / 'postgres.dump'
        dump.write_bytes(dumped.stdout)
        maria = work / 'unused-mariadb.sql'
        maria.write_bytes(b'-- fixture stop boundary\n')

        class Runner:
            def run(self, args, **kwargs):
                if args == ['fixture-compose', 'up', '-d', '--wait', '--wait-timeout', '120',
                            'platform-db', 'engine-db']:
                    return subprocess.CompletedProcess(args, 0, stdout=b'', stderr=b'')
                if 'pg_restore' in args:
                    actual = ['docker', 'exec', '-i', target, *args[args.index('pg_restore'):]]
                    kwargs.setdefault('check', True)
                    kwargs.setdefault('timeout', 45)
                    return subprocess.run(actual, **kwargs)
                if 'engine-db' in args and 'exec' in args:
                    raise PostgresFixtureComplete()
                raise RuntimeError('unexpected fixture command')

        try:
            RestoreManager(work, runner=Runner())._restore_payload(
                {'postgres.dump': dump, 'mariadb.sql': maria}, ['fixture-compose'])
        except PostgresFixtureComplete:
            pass
        else:
            raise RuntimeError('fixture stop boundary was not reached')
        owner = sql(target, "SELECT pg_get_userbyid(relowner) FROM pg_class WHERE relname='flyway_schema_history';")
        migrate = sql(target, 'SET ROLE platform_owner; SELECT count(*) FROM flyway_schema_history;')
        definer = sql(target, 'SET ROLE platform_app; SELECT fixture_definer();')
        app_write = sql(target, 'SET ROLE platform_app; INSERT INTO flyway_schema_history VALUES(2);')
        result['historyOwnerPreserved'] = owner.returncode == 0 and owner.stdout.strip() == b'platform_owner'
        result['migrationRoleCanReadHistory'] = migrate.returncode == 0 and migrate.stdout.strip() == b'1'
        result['securityDefinerOwnerPreserved'] = definer.returncode == 0 and definer.stdout.strip() == b'platform_owner'
        result['appDirectWriteRejected'] = app_write.returncode != 0 and b'permission denied' in app_write.stderr
        if not all(result[key] for key in ('historyOwnerPreserved', 'migrationRoleCanReadHistory',
                                          'securityDefinerOwnerPreserved', 'appDirectWriteRejected')):
            raise RuntimeError('restored owner contract failed')
        # pg_dump does not create global roles. A source-only owner must not
        # silently fall back to the privileged restore connection's identity.
        extra = sql(source, 'CREATE ROLE fixture_missing_owner; CREATE TABLE missing_owner_guard(value integer); '
                            'ALTER TABLE missing_owner_guard OWNER TO fixture_missing_owner;')
        if extra.returncode:
            raise RuntimeError('unknown owner fixture failed')
        missing_dump = command(['docker', 'exec', source, 'pg_dump', '-U', 'postgres',
                                '--format=custom', '--serializable-deferrable', 'platform'])
        if missing_dump.returncode:
            raise RuntimeError('unknown owner dump failed')
        dump.write_bytes(missing_dump.stdout)
        result['unknownOwnerRejected'] = False
        try:
            RestoreManager(work, runner=Runner())._restore_payload(
                {'postgres.dump': dump, 'mariadb.sql': maria}, ['fixture-compose'])
        except RestoreRuntimeError as error:
            cause = error.__cause__
            result['unknownOwnerRejected'] = (str(error) == 'PostgreSQL restore failed'
                and isinstance(cause, subprocess.CalledProcessError)
                and b'role "fixture_missing_owner" does not exist' in (cause.stderr or b''))
        except PostgresFixtureComplete:
            pass
        if not result['unknownOwnerRejected']:
            raise RuntimeError('unknown owner restore was not rejected')
        result['status'] = 'passed'
    except Exception as error:
        # Never persist raw database errors, command output, or test credentials.
        result['errorCategory'] = type(error).__name__
    finally:
        cleanup = not creation_uncertain
        # Creation can time out after Docker accepted it. Discover only this
        # run's random ownership label, including stopped/created containers.
        try:
            discovered = command(['docker', 'ps', '-aq', '--no-trunc', '--filter', 'label=' + ownership_label])
            if discovered.returncode:
                cleanup = False
            for value in discovered.stdout.decode().splitlines():
                if re.fullmatch('[0-9a-f]{64}', value) is None:
                    cleanup = False
                elif value not in containers:
                    containers.append(value)
        except (OSError, subprocess.SubprocessError):
            cleanup = False
        for identifier in containers:
            try:
                removed = command(['docker', 'rm', '-f', '-v', identifier])
                remaining = command(['docker', 'ps', '-aq', '--filter', 'id=' + identifier])
                cleanup = cleanup and removed.returncode == 0 and remaining.returncode == 0 and not remaining.stdout.strip()
            except (OSError, subprocess.SubprocessError):
                cleanup = False
        result['cleanupPassed'] = cleanup
        if cleanup:
            shutil.rmtree(work)
        else:
            result['status'] = 'failed'
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image', choices=('postgres:16.10-alpine', 'postgres:16-alpine'),
                        default='postgres:16.10-alpine')
    parser.add_argument('--evidence', type=Path)
    args = parser.parse_args()
    result = verify(args.image)
    if args.evidence:
        args.evidence.parent.mkdir(parents=True, exist_ok=True)
        args.evidence.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result))
    return 0 if result['status'] == 'passed' and result['cleanupPassed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
