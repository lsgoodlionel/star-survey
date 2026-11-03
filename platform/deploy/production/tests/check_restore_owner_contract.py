#!/usr/bin/env python3
"""Real PostgreSQL owner/personnel checks; confers no restoration eligibility."""
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
import uuid

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
    result = {'schemaVersion': 1, 'scope': 'postgres-restore-owner-personnel-component',
              'status': 'failed', 'cleanupPassed': False, 'image': image,
              'restorationEligible': False, 'releaseAcceptance': False}

    def sql(container, statement):
        return command(['docker', 'exec', '-i', container, 'psql', '-v', 'ON_ERROR_STOP=1',
                        '-U', 'postgres', '-d', 'platform', '-qAt'], input=statement.encode())

    # Force host SCRAM in this fixture: image defaults may trust localhost,
    # which would let positive TCP tests pass without checking any password.
    def tcp_sql(container, role, credential, statement):
        if (role, credential) not in {('platform_owner', 'owner'), ('platform_app', 'app'),
                                      ('platform_owner', 'app')}:
            raise RuntimeError('unexpected fixture credential mapping')
        shell = ('PGPASSWORD="$(cat /fixture/' + credential + ')"; export PGPASSWORD; '
                 'exec psql -h 127.0.0.1 -v ON_ERROR_STOP=1 -U ' + role + ' -d platform -qAt')
        return command(['docker', 'exec', '--user', 'postgres', '-i', container,
                        'sh', '-ec', shell], input=statement.encode())

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
                    '--tmpfs', '/fixture:rw,noexec,nosuid,size=1048576', '--entrypoint', '/bin/sh',
                    '-e', 'POSTGRES_USER=postgres', '-e', 'POSTGRES_DB=postgres',
                    '-e', 'POSTGRES_INITDB_ARGS=--auth-host=scram-sha-256 --auth-local=trust',
                    '-e', 'POSTGRES_PASSWORD_FILE=/fixture/super',
                    '-e', 'PLATFORM_DB_OWNER_PASSWORD_FILE=/fixture/owner',
                    '-e', 'PLATFORM_DB_APP_PASSWORD_FILE=/fixture/app',
                    '-v', str(work) + ':/fixture-input:ro',
                    '-v', str(PRODUCTION / 'init/init-platform-db.sh') +
                    ':/docker-entrypoint-initdb.d/10-platform-roles.sh:ro', image, '-ec',
                    'chmod 700 /fixture; chown postgres:postgres /fixture; '
                    'for key in super owner app; do cp /fixture-input/$key /fixture/$key; '
                    'chmod 600 /fixture/$key; chown postgres:postgres /fixture/$key; done; '
                    'exec /usr/local/bin/docker-entrypoint.sh postgres',
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
        result['credentialsReadableByPostgres'] = True
        result['ownerTcpPasswordVerified'] = True
        result['appTcpPasswordVerified'] = True
        result['wrongTcpPasswordRejected'] = True
        for identifier in containers:
            readable = command(['docker', 'exec', '--user', 'postgres', identifier, 'sh', '-ec',
                'test "$(stat -c %a /fixture)" = 700; '
                'for key in super owner app; do test -r /fixture/$key; '
                'test "$(stat -c %a /fixture/$key)" = 600; '
                'test "$(stat -c %u /fixture/$key)" = "$(id -u)"; done'])
            result['credentialsReadableByPostgres'] &= readable.returncode == 0
            owner_login = tcp_sql(identifier, 'platform_owner', 'owner', 'SELECT current_user;')
            app_login = tcp_sql(identifier, 'platform_app', 'app', 'SELECT current_user;')
            wrong_login = tcp_sql(identifier, 'platform_owner', 'app', 'SELECT 1;')
            result['ownerTcpPasswordVerified'] &= owner_login.returncode == 0 and owner_login.stdout.strip() == b'platform_owner'
            result['appTcpPasswordVerified'] &= app_login.returncode == 0 and app_login.stdout.strip() == b'platform_app'
            result['wrongTcpPasswordRejected'] &= (wrong_login.returncode != 0
                and b'password authentication failed' in wrong_login.stderr)
        if not all(result[key] for key in ('credentialsReadableByPostgres', 'ownerTcpPasswordVerified',
                                          'appTcpPasswordVerified', 'wrongTcpPasswordRejected')):
            raise RuntimeError('fixture password authentication failed')
        source, target = containers
        # Apply the actual SQL migrations as the real TCP-authenticated owner;
        # this is not a synthetic approximation of the personnel tables.
        migrations = PRODUCTION.parents[2] / 'platform/services/business/src/main/resources/db/migration'
        files = sorted(migrations.glob('V*.sql'), key=lambda p: int(p.name.split('__')[0][1:]))
        for migration in files:
            applied = tcp_sql(source, 'platform_owner', 'owner', migration.read_text())
            if applied.returncode:
                result['failedMigration'] = migration.name
                raise RuntimeError('migration fixture failed')
        result['actualMigrationsApplied'] = len(files)
        tenant, connection, first, second, normal, sid, second_sid, normal_sid, revoked_sid = [str(uuid.uuid4()) for _ in range(9)]
        seeded = sql(source, f"""
INSERT INTO tenant(id,code,name,status) VALUES('{tenant}','restore-fixture','restore fixture','provisioning');
UPDATE tenant SET status='active' WHERE id='{tenant}';
INSERT INTO org_connection(id,tenant_id,provider,corp_id,app_id,secret_ref,unknown_user_policy,enabled,created_by)
VALUES('{connection}','{tenant}','wecom','fixture-corp','fixture-app','TEST','jit_staff',true,'fixture');
INSERT INTO identity_binding(principal_id,tenant_id,provider,app_id,external_id)
VALUES ('{first}','{tenant}','wecom','fixture-corp','first'),('{second}','{tenant}','wecom','fixture-corp','second'),('{normal}','{tenant}','wecom','fixture-corp','normal');
INSERT INTO access_member(tenant_id,actor_id,status,uses_seat,invited_by)
VALUES('{tenant}','{first}','active',true,'fixture'),('{tenant}','{second}','active',true,'fixture'),('{tenant}','{normal}','active',true,'fixture');
INSERT INTO org_login_session(id,tenant_id,principal_id,connection_id,expires_at)
VALUES('{sid}','{tenant}','{first}','{connection}',now()+interval '1 hour'),
('{second_sid}','{tenant}','{second}','{connection}',now()+interval '1 hour'),
('{normal_sid}','{tenant}','{normal}','{connection}',now()+interval '1 hour');
INSERT INTO org_login_session(id,tenant_id,principal_id,connection_id,expires_at,revoked_at,revoke_reason)
VALUES('{revoked_sid}','{tenant}','{first}','{connection}',now()+interval '1 hour',now()-interval '5 minutes','logout');
SELECT platform_admin_bootstrap_attributed('{tenant}','{first}','fixture-bootstrap','restore fixture','fixture-operator');
SELECT platform_admin_change_session('{tenant}','{first}','{sid}','{tenant}','{second}',true,'fixture-grant','restore fixture');
SELECT platform_admin_change_session('{tenant}','{first}','{sid}','{tenant}','{second}',false,'fixture-revoke','restore fixture');
""")
        if seeded.returncode:
            result['seedFailed'] = True
            raise RuntimeError('personnel fixture failed')
        seed = sql(source, """
SET ROLE platform_owner;
CREATE TABLE flyway_schema_history(installed_rank integer);
INSERT INTO flyway_schema_history VALUES(1);
REVOKE ALL ON flyway_schema_history FROM platform_app;
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
                if 'psql' in args:
                    actual = ['docker', 'exec', '-i', target, *args[args.index('psql'):]]
                    kwargs.setdefault('check', True)
                    kwargs.setdefault('timeout', 45)
                    return subprocess.run(actual, **kwargs)
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
        migrate = tcp_sql(target, 'platform_owner', 'owner', 'SELECT count(*) FROM flyway_schema_history;')
        definer = tcp_sql(target, 'platform_app', 'app', 'SELECT fixture_definer();')
        app_write = tcp_sql(target, 'platform_app', 'app', 'INSERT INTO flyway_schema_history VALUES(2);')
        result['historyOwnerPreserved'] = owner.returncode == 0 and owner.stdout.strip() == b'platform_owner'
        result['migrationRoleCanReadHistory'] = migrate.returncode == 0 and migrate.stdout.strip() == b'1'
        result['securityDefinerOwnerPreserved'] = definer.returncode == 0 and definer.stdout.strip() == b'platform_owner'
        result['appDirectWriteRejected'] = app_write.returncode != 0 and b'permission denied' in app_write.stderr
        if not all(result[key] for key in ('historyOwnerPreserved', 'migrationRoleCanReadHistory',
                                          'securityDefinerOwnerPreserved', 'appDirectWriteRejected')):
            raise RuntimeError('restored owner contract failed')
        def scalar(statement):
            answer = sql(target, statement)
            if answer.returncode:
                raise RuntimeError('personnel query failed')
            return answer.stdout.decode().splitlines()[-1]
        # The invalidator is called directly as a component: no security floor
        # or signed backup eligibility is fabricated by this test.
        before = scalar('SELECT count(*) FROM platform_admin_audit;')
        previous_revocation = scalar(f"SELECT revoked_at::text FROM org_login_session WHERE id='{revoked_sid}';")
        if scalar(f"SELECT platform_admin_active('{tenant}','{first}');") != 't':
            raise RuntimeError('restored grant fixture not active')
        prepared = sql(target, """
CREATE TABLE platform_security_restore_audit(id bigserial PRIMARY KEY,trace_id text NOT NULL,
operator_db_user text NOT NULL,occurred_at timestamptz NOT NULL DEFAULT now(),reason text NOT NULL);
CREATE FUNCTION fixture_reject_restore_audit() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'fixture audit rejection'; END $$;
CREATE TRIGGER fixture_restore_audit_failure BEFORE INSERT ON platform_security_restore_audit
FOR EACH ROW EXECUTE FUNCTION fixture_reject_restore_audit();
""")
        if prepared.returncode:
            raise RuntimeError('audit fixture failed')
        invalidator = RestoreManager(work, runner=Runner())
        rejected = False
        try:
            invalidator._invalidate_restored_personnel(['fixture-compose'])
        except RestoreRuntimeError as error:
            rejected = str(error) == 'restored personnel invalidation failed'
        result['auditFailureRollsBack'] = (rejected and scalar('SELECT count(*) FROM platform_admin_audit;') == before
            and scalar(f"SELECT platform_admin_active('{tenant}','{first}');") == 't'
            and scalar(f"SELECT revoked_at IS NULL FROM org_login_session WHERE id='{sid}';") == 't'
            and scalar('SELECT count(*) FROM platform_security_restore_audit;') == '0')
        sql(target,'DROP TRIGGER fixture_restore_audit_failure ON platform_security_restore_audit; DROP FUNCTION fixture_reject_restore_audit();')
        invalidator._invalidate_restored_personnel(['fixture-compose'])
        result['personnelGrantsInvalidated'] = scalar('SELECT count(*) FROM platform_admin_grant WHERE active;') == '0'
        result['historicalPersonnelSessionsRevoked'] = scalar(f"SELECT count(*) FROM org_login_session WHERE id IN ('{sid}','{second_sid}') AND revoked_at IS NOT NULL AND revoke_reason='restore';") == '2'
        result['ordinarySessionPreserved'] = scalar(f"SELECT revoked_at IS NULL FROM org_login_session WHERE id='{normal_sid}';") == 't'
        result['restoreAuditRecorded'] = scalar('SELECT count(*) FROM platform_security_restore_audit;') == '1'
        result['priorRevocationPreserved'] = (scalar(f"SELECT revoked_at::text FROM org_login_session WHERE id='{revoked_sid}';") == previous_revocation
            and scalar(f"SELECT revoke_reason FROM org_login_session WHERE id='{revoked_sid}';") == 'logout')
        result['restoreAuditLinked'] = scalar("SELECT count(*) FROM platform_security_restore_audit r JOIN platform_admin_audit a USING(trace_id) WHERE a.action='identity_invalidated' AND a.operator_db_user='postgres' AND r.operator_db_user='postgres';") == '1'
        invalidator._invalidate_restored_personnel(['fixture-compose'])
        result['repeatInvalidationDoesNotReactivate'] = scalar('SELECT count(*) FROM platform_admin_grant WHERE active;') == '0'
        denied = tcp_sql(target,'platform_app','app',f"SELECT platform_admin_recover_attributed('{tenant}','{first}','fixture-forbidden','fixture recovery','fixture-operator');")
        result['runtimeCannotRecover'] = denied.returncode != 0 and b'permission denied' in denied.stderr
        if scalar(f"SELECT platform_admin_recover_attributed('{tenant}','{first}','fixture-controlled','fixture recovery','fixture-operator');") != 't':
            raise RuntimeError('controlled recovery failed')
        old = tcp_sql(target,'platform_app','app',f"SELECT platform_admin_change_session('{tenant}','{first}','{sid}','{tenant}','{second}',true,'fixture-old','fixture old session');")
        result['oldSessionDeniedAfterExplicitRecovery'] = old.returncode != 0 and b'verified session unavailable' in old.stderr
        fresh=str(uuid.uuid4())
        if sql(target,f"INSERT INTO org_login_session(id,tenant_id,principal_id,connection_id,expires_at) VALUES('{fresh}','{tenant}','{first}','{connection}',now()+interval '1 hour');").returncode:
            raise RuntimeError('new session fixture failed')
        new = tcp_sql(target,'platform_app','app',f"SELECT platform_admin_change_session('{tenant}','{first}','{fresh}','{tenant}','{second}',true,'fixture-new','fixture new session');")
        result['explicitRecoveryAndNewSessionRequired'] = new.returncode == 0 and new.stdout.strip() == b't'
        for field in ['auditFailureRollsBack','personnelGrantsInvalidated','historicalPersonnelSessionsRevoked',
                      'ordinarySessionPreserved','restoreAuditRecorded','priorRevocationPreserved','restoreAuditLinked','repeatInvalidationDoesNotReactivate',
                      'runtimeCannotRecover','oldSessionDeniedAfterExplicitRecovery','explicitRecoveryAndNewSessionRequired']:
            if not result[field]:
                raise RuntimeError('personnel safety assertion failed')
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
