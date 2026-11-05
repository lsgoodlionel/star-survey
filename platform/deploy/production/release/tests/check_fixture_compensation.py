#!/usr/bin/env python3
"""Exercise a compensation proposal against actual migrations in a disposable CI DB.

This is not a candidate fixture implementation or an authorization to run it on
an installation. It checks only the schema subset used in this experiment.
"""
import os
from pathlib import Path
import re
import secrets
import subprocess
import time
import uuid
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fixture_compensation import build_compensation_sql


def main():
    if os.environ.get('GITHUB_ACTIONS') != 'true':
        raise RuntimeError('CI service test only')
    port = os.environ.get('FIXTURE_PG_PORT', '')
    if not re.fullmatch(r'[0-9]{1,5}', port) or not 1 <= int(port) <= 65535:
        raise RuntimeError('invalid isolated service port')
    database = 'fixture_' + secrets.token_hex(12)
    env = dict(os.environ, PGPASSWORD='fixture-only-password', PGCONNECT_TIMEOUT='5',
               PGOPTIONS='-c statement_timeout=10000')
    def execute(sql, db='postgres', check=True):
        command = ['psql', '-X', '-q', '-A', '-t', '-h', '127.0.0.1', '-p', port,
                   '-U', 'postgres', '-d', db, '-v', 'ON_ERROR_STOP=1']
        result = subprocess.run(command, input=sql, text=True, env=env, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, timeout=30)
        if check and result.returncode:
            raise RuntimeError('isolated database operation failed: ' + result.stderr[-2000:])
        return result
    # Dedicated service is freshly created by this CI job; these are test roles.
    execute("DO $$ BEGIN CREATE ROLE platform_owner NOLOGIN; EXCEPTION WHEN duplicate_object THEN NULL; END $$; "
            "DO $$ BEGIN CREATE ROLE platform_app NOLOGIN; EXCEPTION WHEN duplicate_object THEN NULL; END $$;")
    execute('CREATE DATABASE ' + database + ' OWNER platform_owner')
    try:
        migration_dir = Path(__file__).resolve().parents[4] / 'services/business/src/main/resources/db/migration'
        versions = (1, 100, 101, 103, 104, 105, 106, 107, 108, 110, 111, 200, 201, 400, 401)
        migrations = []
        for version in versions:
            matches = list(migration_dir.glob('V' + str(version) + '__*.sql'))
            if len(matches) != 1:
                raise RuntimeError('migration not uniquely found')
            migrations.append(matches[0].read_text())
        execute('SET ROLE platform_owner;\n' + '\n'.join(migrations), database)
        tenant, other_tenant, owner_plan, fixture_plan, principal, connection = (str(uuid.uuid4()) for _ in range(6))
        original_sub, fixture_sub, restore_sub, grant, session, owner_grant, project, project_grant, other_grant = (str(uuid.uuid4()) for _ in range(9))
        handoff_hash, browser_hash = secrets.token_hex(32), secrets.token_hex(32)
        scope = "SELECT set_config('app.tenant_id','" + tenant + "',true);"
        setup = f"""
BEGIN; SET LOCAL ROLE platform_owner; {scope}
INSERT INTO tenant(id,code,name,status) VALUES ('{tenant}','ci-probe','CI probe','provisioning');
UPDATE tenant SET status='active' WHERE id='{tenant}';
INSERT INTO tenant(id,code,name,status) VALUES ('{other_tenant}','ci-other','CI other','provisioning');
UPDATE tenant SET status='active' WHERE id='{other_tenant}';
INSERT INTO plan_version(id,plan_code,version,capabilities,quotas,export_window_days)
 VALUES ('{owner_plan}','ci-probe',1,ARRAY['survey.read','survey.write','response.collect','response.export'],
 '{{"member.seats":1,"response.valid_completed":100}}',1);
INSERT INTO subscription(id,tenant_id,plan_version_id,kind,starts_at,ends_at)
 VALUES ('{original_sub}','{tenant}','{owner_plan}','trial',now()-interval '1 day',now()+interval '10 days');
INSERT INTO access_member(tenant_id,actor_id,status,uses_seat,invited_by)
 VALUES ('{tenant}','production-probe-owner','active',true,'ci');
INSERT INTO access_grant(tenant_id,id,actor_id,role_code,resource_id,granted_by)
 VALUES ('{tenant}','{owner_grant}','production-probe-owner','tenant_owner',NULL,'ci');
SELECT set_config('app.tenant_id','{other_tenant}',true);
INSERT INTO access_member(tenant_id,actor_id,status,uses_seat,invited_by)
 VALUES ('{other_tenant}','{principal}','active',true,'ci');
INSERT INTO access_grant(tenant_id,id,actor_id,role_code,resource_id,granted_by,expires_at)
 VALUES ('{other_tenant}','{other_grant}','{principal}','org_admin',NULL,'ci',now()+interval '15 minutes');
COMMIT;
"""
        execute(setup, database)
        provision = f"""
BEGIN; SET LOCAL ROLE platform_app; {scope}
INSERT INTO plan_version(id,plan_code,version,capabilities,quotas,export_window_days)
 SELECT '{fixture_plan}','ci-fixture',1,capabilities,jsonb_set(quotas,'{{member.seats}}','2'),export_window_days
 FROM plan_version WHERE id='{owner_plan}';
INSERT INTO subscription(id,tenant_id,plan_version_id,kind,starts_at,ends_at)
 SELECT '{fixture_sub}','{tenant}','{fixture_plan}',kind,now(),ends_at FROM subscription WHERE id='{original_sub}';
INSERT INTO org_connection(id,tenant_id,provider,corp_id,app_id,secret_ref,unknown_user_policy,enabled,created_by)
 VALUES ('{connection}','{tenant}','wecom','ci-corp','ci-app','CI_SECRET','require_preauthorized',true,'ci');
INSERT INTO identity_binding(principal_id,tenant_id,provider,app_id,external_id)
 VALUES ('{principal}','{tenant}','wecom','ci-corp','ci-user');
INSERT INTO access_member(tenant_id,actor_id,status,uses_seat,invited_by)
 VALUES ('{tenant}','{principal}','active',true,'ci');
INSERT INTO access_grant(tenant_id,id,actor_id,role_code,resource_id,granted_by,expires_at)
 VALUES ('{tenant}','{grant}','{principal}','org_admin',NULL,'ci',now()+interval '15 minutes');
-- Model the project-level creator grant generated during a real author journey.
INSERT INTO access_resource(tenant_id,id,kind,parent_id,name)
 VALUES ('{tenant}','{project}','project',NULL,'CI test project');
INSERT INTO access_grant(tenant_id,id,actor_id,role_code,resource_id,granted_by,expires_at)
 VALUES ('{tenant}','{project_grant}','{principal}','project_manager','{project}','ci',now()+interval '15 minutes');
INSERT INTO org_login_handoff(handoff_hash,browser_hash,tenant_id,principal_id,connection_id,provider,expires_at)
 VALUES ('{handoff_hash}','{browser_hash}','{tenant}','{principal}','{connection}','wecom',now()+interval '1 minute');
INSERT INTO org_login_session(id,tenant_id,principal_id,connection_id,expires_at)
 VALUES ('{session}','{tenant}','{principal}','{connection}',now()+interval '1 hour');
COMMIT;
"""
        # The browser author needs tenant-wide project creation and export;
        # the earlier project_manager role lacks manage-settings.
        permissions = execute("SELECT count(*) FROM access_role_permission WHERE role_code='org_admin' "
                              "AND permission_code IN ('manage-settings','edit','publish','export-raw-responses')", database).stdout
        if permissions.strip() != '4':
            raise RuntimeError('fixture role lacks required author permissions')
        # First prove rollback on an injected error inside the transaction.
        failed = execute(provision.replace('COMMIT;', 'SELECT 1/0; COMMIT;'), database, check=False)
        if failed.returncode == 0:
            raise RuntimeError('injected transaction did not fail')
        check = execute(f"BEGIN; SET LOCAL ROLE platform_owner; {scope} SELECT count(*) FROM identity_binding WHERE principal_id='{principal}'; COMMIT;", database).stdout
        if check.strip().splitlines()[-1] != '0':
            raise RuntimeError('failed transaction left a principal')
        # Simulate a successful commit followed by a lost/error result.
        failed = execute(provision + '\\echo committed-before-error\nSELECT 1/0;', database, check=False)
        if failed.returncode == 0:
            raise RuntimeError('injected ambiguous result did not fail')
        # Product runtime privileges deliberately do not permit the physical
        # lock needed to block uncooperative subscription writers. Keep this as
        # a negative contract; it is not proof of concurrent compensation safety.
        denied_lock = execute(f"""BEGIN; SET LOCAL ROLE platform_app; {scope}
LOCK TABLE subscription IN SHARE ROW EXCLUSIVE MODE; COMMIT;""", database, check=False)
        if denied_lock.returncode == 0 or 'permission denied for table subscription' not in denied_lock.stderr:
            raise RuntimeError('subscription lock privilege changed; concurrency design requires review')
        print('runtime role cannot take physical subscription lock; concurrent compensation remains blocked')
        # Use two real database sessions to show that the existing member lock
        # does not serialize an unrelated subscription writer. The concurrent
        # insertion rolls back and the experiment cannot change final state.
        lock_app = 'fixture_access_lock_' + secrets.token_hex(12)
        holder_sql = f"""BEGIN; SET LOCAL ROLE platform_app; {scope}
SELECT pg_advisory_xact_lock(hashtextextended('access.members:{tenant}',0)); SELECT pg_sleep(5); ROLLBACK;"""
        command = ['psql', '-X', '-q', '-h', '127.0.0.1', '-p', port, '-U', 'postgres',
                   '-d', database, '-v', 'ON_ERROR_STOP=1', '-c', holder_sql]
        holder = subprocess.Popen(command, env=dict(env, PGAPPNAME=lock_app), stdin=subprocess.DEVNULL,
                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                observed = execute("SELECT count(*) FROM pg_stat_activity WHERE application_name='" + lock_app +
                                   "' AND datname='" + database + "' AND wait_event='PgSleep'").stdout.strip()
                if observed == '1':
                    break
                if holder.poll() is not None:
                    raise RuntimeError('member-lock holder exited before concurrent test')
                time.sleep(.05)
            else:
                raise RuntimeError('member-lock holder was not observed')
            concurrent_id = str(uuid.uuid4())
            result = execute(f"""BEGIN; SET LOCAL ROLE platform_app; {scope}
SET LOCAL lock_timeout='1s';
INSERT INTO subscription(id,tenant_id,plan_version_id,kind,starts_at,ends_at)
 SELECT '{concurrent_id}','{tenant}','{owner_plan}',kind,now(),ends_at FROM subscription WHERE id='{original_sub}';
ROLLBACK;""", database, check=False)
            if result.returncode != 0 or holder.poll() is not None:
                raise RuntimeError('concurrent subscription observation was ambiguous')
            if holder.wait(timeout=10) != 0:
                raise RuntimeError('member-lock holder failed')
        finally:
            if holder.poll() is None:
                holder.kill()
                holder.wait(timeout=10)

        compensate = build_compensation_sql(
            tenant=tenant, owner_plan=owner_plan, fixture_plan=fixture_plan, principal=principal,
            connection=connection, original_sub=original_sub, fixture_sub=fixture_sub,
            restore_sub=restore_sub, owner_grant=owner_grant,
            handoff_hash=handoff_hash, browser_hash=browser_hash)

        # Prove the author role is tenant-wide and expires; the later cleanup
        # must remove it alongside any project-specific creator grants.
        author = execute(f"""BEGIN; SET LOCAL ROLE platform_app; {scope}
SELECT count(*) FROM access_grant WHERE tenant_id='{tenant}' AND id='{grant}' AND actor_id='{principal}'
 AND role_code='org_admin' AND resource_id IS NULL AND expires_at>now() AND expires_at<=now()+interval '15 minutes';
COMMIT;""", database).stdout
        if author.strip().splitlines()[-1] != '1':
            raise RuntimeError('fixture author role is not constrained as expected')
        # A mismatched handoff must roll back atomically without consuming the
        # other principal's handoff or revoking this fixture identity.
        other_principal = str(uuid.uuid4())
        other_handoff, other_browser = secrets.token_hex(32), secrets.token_hex(32)
        execute(f"""BEGIN; SET LOCAL ROLE platform_app; {scope}
INSERT INTO identity_binding(principal_id,tenant_id,provider,app_id,external_id)
 VALUES ('{other_principal}','{tenant}','wecom','ci-corp','ci-other-user');
INSERT INTO org_login_handoff(handoff_hash,browser_hash,tenant_id,principal_id,connection_id,provider,expires_at)
 VALUES ('{other_handoff}','{other_browser}','{tenant}','{other_principal}','{connection}','wecom',now()+interval '1 minute');
COMMIT;""", database)
        mismatch = build_compensation_sql(
            tenant=tenant, owner_plan=owner_plan, fixture_plan=fixture_plan, principal=principal,
            connection=connection, original_sub=original_sub, fixture_sub=fixture_sub,
            restore_sub=restore_sub, owner_grant=owner_grant,
            handoff_hash=other_handoff, browser_hash=other_browser)
        if execute(mismatch, database, check=False).returncode == 0:
            raise RuntimeError('mismatched handoff was accepted')
        mismatch_state = execute(f"""BEGIN; SET LOCAL ROLE platform_owner; {scope}
SELECT EXISTS (SELECT 1 FROM org_login_handoff WHERE handoff_hash='{other_handoff}' AND consumed_at IS NULL)
 AND NOT EXISTS (SELECT 1 FROM identity_binding_revocation WHERE tenant_id='{tenant}' AND principal_id='{principal}' AND reinstated_at IS NULL);
COMMIT;""", database).stdout
        if mismatch_state.strip().splitlines()[-1] != 't':
            raise RuntimeError('mismatched handoff changed database state')

        # Missing, expired and already-consumed handoffs are indistinguishable
        # to the application role. Initial compensation must reject all three.
        for handoff_state in ('missing', 'expired', 'consumed'):
            test_hash, test_browser = secrets.token_hex(32), secrets.token_hex(32)
            if handoff_state != 'missing':
                expiry = "now()-interval '1 second'" if handoff_state == 'expired' else "now()+interval '1 minute'"
                execute(f"""BEGIN; SET LOCAL ROLE platform_app; {scope}
INSERT INTO org_login_handoff(handoff_hash,browser_hash,tenant_id,principal_id,connection_id,provider,expires_at)
 VALUES ('{test_hash}','{test_browser}','{tenant}','{principal}','{connection}','wecom',{expiry});
COMMIT;""", database)
                if handoff_state == 'consumed':
                    execute(f"""BEGIN; SET LOCAL ROLE platform_app; {scope}
SELECT * FROM org_login_handoff_consume('{tenant}','{test_hash}','{test_browser}',now()); COMMIT;""", database)
            ambiguous = build_compensation_sql(
                tenant=tenant, owner_plan=owner_plan, fixture_plan=fixture_plan, principal=principal,
                connection=connection, original_sub=original_sub, fixture_sub=fixture_sub,
                restore_sub=restore_sub, owner_grant=owner_grant,
                handoff_hash=test_hash, browser_hash=test_browser)
            if execute(ambiguous, database, check=False).returncode == 0:
                raise RuntimeError('ambiguous initial handoff accepted: ' + handoff_state)
        wrong_connection = build_compensation_sql(
            tenant=tenant, owner_plan=owner_plan, fixture_plan=fixture_plan, principal=principal,
            connection=str(uuid.uuid4()), original_sub=original_sub, fixture_sub=fixture_sub,
            restore_sub=restore_sub, owner_grant=owner_grant,
            handoff_hash=handoff_hash, browser_hash=browser_hash)
        if execute(wrong_connection, database, check=False).returncode == 0:
            raise RuntimeError('wrong fixture connection was accepted')
        wrong_principal = build_compensation_sql(
            tenant=tenant, owner_plan=owner_plan, fixture_plan=fixture_plan, principal=other_principal,
            connection=connection, original_sub=original_sub, fixture_sub=fixture_sub,
            restore_sub=restore_sub, owner_grant=owner_grant,
            handoff_hash=handoff_hash, browser_hash=browser_hash)
        if execute(wrong_principal, database, check=False).returncode == 0:
            raise RuntimeError('wrong fixture principal was accepted')
        # An injected failure inside compensation must roll back every change.
        if execute(compensate.replace('COMMIT;', 'SELECT 1/0; COMMIT;'), database, check=False).returncode == 0:
            raise RuntimeError('injected compensation failure was accepted')
        unchanged = execute(f"""BEGIN; SET LOCAL ROLE platform_owner; {scope}
SELECT EXISTS (SELECT 1 FROM org_login_handoff WHERE handoff_hash='{handoff_hash}' AND consumed_at IS NULL)
 AND NOT EXISTS (SELECT 1 FROM identity_binding_revocation WHERE tenant_id='{tenant}' AND principal_id IN ('{principal}'::uuid,'{other_principal}'::uuid) AND reinstated_at IS NULL)
 AND EXISTS (SELECT 1 FROM access_grant WHERE tenant_id='{tenant}' AND actor_id='{principal}' AND id='{grant}');
COMMIT;""", database).stdout
        if unchanged.strip().splitlines()[-1] != 't':
            raise RuntimeError('failed compensation was not rolled back')

        # A pre-existing subscription change must abort the whole compensation,
        # without revoking the fixture or overwriting a different plan. The
        # surrounding synthetic transaction is rolled back on psql failure.
        for kind in ('trial', 'cancelled'):
            other_sub = str(uuid.uuid4())
            conflict = f"""BEGIN; SET LOCAL ROLE platform_app; {scope}
INSERT INTO subscription(id,tenant_id,plan_version_id,kind,starts_at,ends_at)
 SELECT '{other_sub}','{tenant}','{fixture_plan}','{kind}',now(),
        CASE WHEN '{kind}'='cancelled' THEN now() ELSE ends_at END
 FROM subscription WHERE id='{original_sub}';
""" + compensate
            if execute(conflict, database, check=False).returncode == 0:
                raise RuntimeError('concurrent same-plan subscription change was accepted')
            state = execute(f"""BEGIN; SET LOCAL ROLE platform_app; {scope}
SELECT (SELECT id='{fixture_sub}' FROM subscription WHERE tenant_id='{tenant}' AND starts_at<=now() ORDER BY starts_at DESC,seq DESC LIMIT 1)
 AND NOT EXISTS (SELECT 1 FROM identity_binding_revocation WHERE tenant_id='{tenant}' AND principal_id='{principal}' AND reinstated_at IS NULL);
COMMIT;""", database).stdout
            if state.strip().splitlines()[-1] != 't':
                raise RuntimeError('failed compensation changed fixture state')
        # A committed compensation followed by an error models a lost response;
        # retry must converge safely without duplicating the restore period.
        if execute(compensate + '\\echo committed-before-error\nSELECT 1/0;', database, check=False).returncode == 0:
            raise RuntimeError('post-commit response-loss injection did not fail')
        execute(compensate, database)
        verified = execute(f"""BEGIN; SET LOCAL ROLE platform_app; {scope}
SELECT (SELECT id='{restore_sub}' AND plan_version_id='{owner_plan}' AND kind='trial'
        AND ends_at=(SELECT ends_at FROM subscription WHERE id='{original_sub}')
        FROM subscription WHERE tenant_id='{tenant}' AND starts_at<=now() ORDER BY starts_at DESC,seq DESC LIMIT 1)
 AND (SELECT count(*)=3 FROM subscription WHERE tenant_id='{tenant}')
 AND EXISTS (SELECT 1 FROM subscription WHERE id='{original_sub}' AND tenant_id='{tenant}' AND plan_version_id='{owner_plan}' AND kind='trial')
 AND EXISTS (SELECT 1 FROM subscription WHERE id='{fixture_sub}' AND tenant_id='{tenant}' AND plan_version_id='{fixture_plan}' AND kind='trial'
             AND ends_at=(SELECT ends_at FROM subscription WHERE id='{original_sub}'))
 AND EXISTS (SELECT 1 FROM access_member m JOIN access_grant g ON g.tenant_id=m.tenant_id AND g.actor_id=m.actor_id
             WHERE m.tenant_id='{tenant}' AND m.actor_id='production-probe-owner' AND m.status='active' AND m.uses_seat
             AND g.id='{owner_grant}' AND g.role_code='tenant_owner' AND g.resource_id IS NULL AND g.expires_at IS NULL)
 AND NOT EXISTS (SELECT 1 FROM access_grant WHERE tenant_id='{tenant}' AND actor_id='{principal}')
 AND EXISTS (SELECT 1 FROM org_connection WHERE tenant_id='{tenant}' AND id='{connection}' AND NOT enabled)
 AND (SELECT count(*)=1 FROM access_member WHERE tenant_id='{tenant}' AND uses_seat AND status<>'removed')
 AND EXISTS (SELECT 1 FROM identity_binding_revocation WHERE tenant_id='{tenant}' AND principal_id='{principal}' AND reinstated_at IS NULL)
 AND NOT EXISTS (SELECT 1 FROM org_login_session WHERE tenant_id='{tenant}' AND principal_id='{principal}' AND revoked_at IS NULL)
 AND NOT EXISTS (SELECT 1 FROM org_login_handoff_consume('{tenant}','{handoff_hash}','{browser_hash}',now()));
COMMIT;""", database).stdout
        if verified.strip().splitlines()[-1] != 't':
            raise RuntimeError('compensation state verification failed')
        # An identical actor string in another tenant must remain untouched.
        other_scope = "SELECT set_config('app.tenant_id','" + other_tenant + "',true);"
        other_state = execute(f"""BEGIN; SET LOCAL ROLE platform_app; {other_scope}
SELECT EXISTS (SELECT 1 FROM access_member WHERE tenant_id='{other_tenant}' AND actor_id='{principal}' AND status='active')
 AND EXISTS (SELECT 1 FROM access_grant WHERE tenant_id='{other_tenant}' AND actor_id='{principal}' AND id='{other_grant}');
COMMIT;""", database).stdout
        if other_state.strip().splitlines()[-1] != 't':
            raise RuntimeError('compensation touched another tenant')
        print('actual migrated schema and platform_app role: rollback, ambiguous commit and idempotent compensation verified')
    finally:
        execute('DROP DATABASE ' + database + ' WITH (FORCE)')


if __name__ == '__main__':
    main()
