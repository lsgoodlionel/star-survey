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
import uuid


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
        original_sub, fixture_sub, restore_sub, grant, session, owner_grant, project, project_grant = (str(uuid.uuid4()) for _ in range(8))
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

        compensate = f"""
BEGIN; SET LOCAL ROLE platform_app; {scope}
SELECT pg_advisory_xact_lock(hashtextextended('access.members:{tenant}',0));
DO $$ DECLARE current_id uuid; current_plan uuid; current_kind text; current_end timestamptz;
 original_kind text; original_end timestamptz; BEGIN
 SELECT kind, ends_at INTO original_kind, original_end FROM subscription
   WHERE tenant_id='{tenant}' AND id='{original_sub}' AND plan_version_id='{owner_plan}';
 IF original_kind IS DISTINCT FROM 'trial' OR original_end IS NULL OR original_end <= now() THEN
   RAISE EXCEPTION 'original subscription changed';
 END IF;
 SELECT id,plan_version_id,kind,ends_at INTO current_id,current_plan,current_kind,current_end
   FROM subscription WHERE tenant_id='{tenant}' AND starts_at <= now()
   ORDER BY starts_at DESC,seq DESC LIMIT 1;
 IF current_id IS NULL OR current_id NOT IN ('{original_sub}'::uuid,'{fixture_sub}'::uuid,'{restore_sub}'::uuid)
    OR current_kind IS DISTINCT FROM original_kind OR current_end IS DISTINCT FROM original_end
    OR (current_id='{fixture_sub}'::uuid AND current_plan IS DISTINCT FROM '{fixture_plan}'::uuid)
    OR (current_id IN ('{original_sub}'::uuid,'{restore_sub}'::uuid) AND current_plan IS DISTINCT FROM '{owner_plan}'::uuid)
    OR NOT EXISTS (SELECT 1 FROM access_member m JOIN access_grant g ON g.tenant_id=m.tenant_id AND g.actor_id=m.actor_id
       WHERE m.tenant_id='{tenant}' AND m.actor_id='production-probe-owner' AND m.status='active' AND m.uses_seat
       AND g.id='{owner_grant}' AND g.role_code='tenant_owner' AND g.resource_id IS NULL AND g.expires_at IS NULL) THEN
   RAISE EXCEPTION 'unexpected original or current state';
 END IF;
 IF EXISTS (SELECT 1 FROM identity_binding WHERE tenant_id='{tenant}' AND principal_id='{principal}') THEN
   INSERT INTO identity_binding_revocation(tenant_id,principal_id,reason,revoked_by)
     VALUES ('{tenant}','{principal}','admin','ci-cleanup')
     ON CONFLICT (tenant_id,principal_id) WHERE reinstated_at IS NULL DO NOTHING;
   PERFORM * FROM org_login_handoff_consume('{tenant}','{handoff_hash}','{browser_hash}',now());
   UPDATE org_login_session SET revoked_at=now(),revoke_reason='binding_revoked'
     WHERE tenant_id='{tenant}' AND principal_id='{principal}' AND revoked_at IS NULL;
   DELETE FROM access_grant WHERE tenant_id='{tenant}' AND actor_id='{principal}';
   UPDATE org_connection SET enabled=false,updated_at=now() WHERE tenant_id='{tenant}' AND id='{connection}';
   UPDATE access_member SET status='removed',updated_at=now()
     WHERE tenant_id='{tenant}' AND actor_id='{principal}';
 END IF;
 IF current_id='{fixture_sub}'::uuid THEN
   INSERT INTO subscription(id,tenant_id,plan_version_id,kind,starts_at,ends_at)
   SELECT '{restore_sub}','{tenant}','{owner_plan}',kind,now(),ends_at FROM subscription WHERE id='{original_sub}';
 END IF;
END $$;
COMMIT;
"""
        # A concurrent subscription change must abort the whole compensation,
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
        execute(compensate, database)
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
        print('actual migrated schema and platform_app role: rollback, ambiguous commit and idempotent compensation verified')
    finally:
        execute('DROP DATABASE ' + database + ' WITH (FORCE)')


if __name__ == '__main__':
    main()
