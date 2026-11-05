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
        migration_dir = Path(__file__).resolve().parents[3] / 'services/business/src/main/resources/db/migration'
        versions = (1, 100, 101, 103, 104, 105, 106, 107, 108, 110, 111, 200, 201, 400, 401)
        migrations = []
        for version in versions:
            matches = list(migration_dir.glob('V' + str(version) + '__*.sql'))
            if len(matches) != 1:
                raise RuntimeError('migration not uniquely found')
            migrations.append(matches[0].read_text())
        execute('SET ROLE platform_owner;\n' + '\n'.join(migrations), database)
        tenant, owner_plan, fixture_plan, principal, connection = (str(uuid.uuid4()) for _ in range(5))
        original_sub, fixture_sub, restore_sub, grant, session = (str(uuid.uuid4()) for _ in range(5))
        handoff_hash, browser_hash = secrets.token_hex(32), secrets.token_hex(32)
        scope = "SELECT set_config('app.tenant_id','" + tenant + "',true);"
        setup = f"""
BEGIN; SET LOCAL ROLE platform_owner; {scope}
INSERT INTO tenant(id,code,name,status) VALUES ('{tenant}','ci-probe','CI probe','provisioning');
UPDATE tenant SET status='active' WHERE id='{tenant}';
INSERT INTO plan_version(id,plan_code,version,capabilities,quotas,export_window_days)
 VALUES ('{owner_plan}','ci-probe',1,ARRAY['survey.read','survey.write','response.collect','response.export'],
 '{{"member.seats":1,"response.valid_completed":100}}',1);
INSERT INTO subscription(id,tenant_id,plan_version_id,kind,starts_at,ends_at)
 VALUES ('{original_sub}','{tenant}','{owner_plan}','trial',now()-interval '1 day',now()+interval '10 days');
INSERT INTO access_member(tenant_id,actor_id,status,uses_seat,invited_by)
 VALUES ('{tenant}','production-probe-owner','active',true,'ci');
COMMIT;
"""
        execute(setup, database)
        provision = f"""
BEGIN; SET LOCAL ROLE platform_owner; {scope}
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
INSERT INTO access_grant(tenant_id,id,actor_id,role_code,resource_id,granted_by)
 VALUES ('{tenant}','{grant}','{principal}','project_manager',NULL,'ci');
INSERT INTO org_login_handoff(handoff_hash,browser_hash,tenant_id,principal_id,connection_id,provider,expires_at)
 VALUES ('{handoff_hash}','{browser_hash}','{tenant}','{principal}','{connection}','wecom',now()+interval '1 minute');
INSERT INTO org_login_session(id,tenant_id,principal_id,connection_id,expires_at)
 VALUES ('{session}','{tenant}','{principal}','{connection}',now()+interval '1 hour');
COMMIT;
"""
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
        compensate = f"""
BEGIN; SET LOCAL ROLE platform_owner; {scope}
SELECT pg_advisory_xact_lock(hashtextextended('access.members:{tenant}',0));
DO $$ DECLARE current_plan uuid; BEGIN
 SELECT plan_version_id INTO current_plan FROM subscription WHERE tenant_id='{tenant}' AND starts_at <= now()
 ORDER BY starts_at DESC,seq DESC LIMIT 1;
 IF current_plan NOT IN ('{owner_plan}'::uuid,'{fixture_plan}'::uuid) OR current_plan IS NULL THEN
   RAISE EXCEPTION 'unexpected current plan';
 END IF;
 IF EXISTS (SELECT 1 FROM identity_binding WHERE tenant_id='{tenant}' AND principal_id='{principal}') THEN
   INSERT INTO identity_binding_revocation(tenant_id,principal_id,reason,revoked_by)
     VALUES ('{tenant}','{principal}','admin','ci-cleanup')
     ON CONFLICT (tenant_id,principal_id) WHERE reinstated_at IS NULL DO NOTHING;
   PERFORM * FROM org_login_handoff_consume('{tenant}','{handoff_hash}','{browser_hash}',now());
   UPDATE org_login_session SET revoked_at=now(),revoke_reason='binding_revoked'
     WHERE tenant_id='{tenant}' AND principal_id='{principal}' AND revoked_at IS NULL;
   UPDATE access_member SET status='removed',updated_at=now()
     WHERE tenant_id='{tenant}' AND actor_id='{principal}';
 END IF;
 IF current_plan='{fixture_plan}'::uuid THEN
   INSERT INTO subscription(id,tenant_id,plan_version_id,kind,starts_at,ends_at)
   SELECT '{restore_sub}','{tenant}','{owner_plan}',kind,now(),ends_at FROM subscription WHERE id='{original_sub}';
 END IF;
END $$;
COMMIT;
"""
        execute(compensate, database)
        execute(compensate, database)
        verified = execute(f"""BEGIN; SET LOCAL ROLE platform_owner; {scope}
SELECT (SELECT plan_version_id='{owner_plan}' FROM subscription WHERE tenant_id='{tenant}' AND starts_at<=now() ORDER BY starts_at DESC,seq DESC LIMIT 1)
 AND (SELECT count(*)=1 FROM access_member WHERE tenant_id='{tenant}' AND uses_seat AND status<>'removed')
 AND EXISTS (SELECT 1 FROM identity_binding_revocation WHERE tenant_id='{tenant}' AND principal_id='{principal}' AND reinstated_at IS NULL)
 AND NOT EXISTS (SELECT 1 FROM org_login_session WHERE tenant_id='{tenant}' AND principal_id='{principal}' AND revoked_at IS NULL)
 AND NOT EXISTS (SELECT 1 FROM org_login_handoff WHERE tenant_id='{tenant}' AND principal_id='{principal}' AND consumed_at IS NULL AND expires_at>now());
COMMIT;""", database).stdout
        if verified.strip().splitlines()[-1] != 't':
            raise RuntimeError('compensation state verification failed')
        print('actual migrated schema: transaction rollback, ambiguous commit and idempotent compensation verified')
    finally:
        execute('DROP DATABASE ' + database + ' WITH (FORCE)')


if __name__ == '__main__':
    main()
