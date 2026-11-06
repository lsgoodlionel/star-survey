"""Build a test fixture transaction; never authorize or execute against a target.

Callers must establish a dedicated hosted instance and exclusive writer control,
fix the database session identity, and retain a durable pending checkpoint before
executing. An uncertain commit requires inspection and compensation, not retry.
"""
from datetime import datetime, timedelta, timezone
import re

from fixture_compensation import _random_uuid


def build_fixture_provision_sql(*, tenant, owner_plan, original_sub, owner_grant,
                                fixture_plan, fixture_sub, principal, connection,
                                author_grant, handoff_hash, browser_hash, expires_at):
    ids = (tenant, owner_plan, original_sub, owner_grant, fixture_plan, fixture_sub,
           principal, connection, author_grant)
    for value in ids:
        _random_uuid(value)
    if len(set(ids)) != len(ids):
        raise ValueError('fixture identities must be distinct')
    if any(not isinstance(value, str) or re.fullmatch(r'[0-9a-f]{64}', value) is None
           for value in (handoff_hash, browser_hash)) or handoff_hash == browser_hash:
        raise ValueError('fixture handoff hashes invalid')
    if (not isinstance(expires_at, datetime) or expires_at.tzinfo is None or
            expires_at.utcoffset() != timedelta(0)):
        raise ValueError('fixture expiration invalid')
    expiry = expires_at.astimezone(timezone.utc).isoformat()
    scope = "SELECT set_config('app.tenant_id','" + tenant + "',true);"
    external = 'fixture-' + connection
    return f"""
BEGIN; SET LOCAL ROLE platform_app; {scope}
SELECT pg_advisory_xact_lock(hashtextextended('access.members:{tenant}',0));
DO $fixture$ BEGIN
 IF NOT EXISTS (SELECT 1 FROM subscription s JOIN plan_version p ON p.id=s.plan_version_id
   WHERE s.tenant_id='{tenant}' AND s.id='{original_sub}' AND s.plan_version_id='{owner_plan}'
   AND s.kind='trial' AND s.starts_at<=now() AND s.ends_at>now() AND p.quotas->>'member.seats'='1'
   AND s.id=(SELECT id FROM subscription WHERE tenant_id='{tenant}' AND starts_at<=now() ORDER BY starts_at DESC,seq DESC LIMIT 1))
 OR (SELECT count(*) FROM access_member WHERE tenant_id='{tenant}' AND uses_seat AND status<>'removed')<>1
 OR NOT EXISTS (SELECT 1 FROM access_member m JOIN access_grant g ON g.tenant_id=m.tenant_id AND g.actor_id=m.actor_id
   WHERE m.tenant_id='{tenant}' AND m.actor_id='production-probe-owner' AND m.status='active' AND m.uses_seat
   AND g.id='{owner_grant}' AND g.role_code='tenant_owner' AND g.resource_id IS NULL AND g.expires_at IS NULL)
 OR EXISTS (SELECT 1 FROM access_member WHERE tenant_id='{tenant}' AND actor_id='{principal}')
 OR '{expiry}'::timestamptz <= now() OR '{expiry}'::timestamptz > now()+interval '60 seconds' THEN
   RAISE EXCEPTION 'fixture initial state unconfirmed';
 END IF;
 INSERT INTO plan_version(id,plan_code,version,capabilities,quotas,export_window_days)
 SELECT '{fixture_plan}','fixture-{fixture_plan}',1,capabilities,jsonb_set(quotas,'{{member.seats}}','2'),export_window_days
 FROM plan_version WHERE id='{owner_plan}';
 INSERT INTO subscription(id,tenant_id,plan_version_id,kind,starts_at,ends_at)
 SELECT '{fixture_sub}','{tenant}','{fixture_plan}',kind,now(),ends_at FROM subscription WHERE id='{original_sub}';
 INSERT INTO org_connection(id,tenant_id,provider,corp_id,app_id,secret_ref,unknown_user_policy,enabled,created_by)
 VALUES ('{connection}','{tenant}','wecom','{external}','{external}','CI_FIXTURE','require_preauthorized',true,'ci-fixture');
 INSERT INTO identity_binding(principal_id,tenant_id,provider,app_id,external_id)
 VALUES ('{principal}','{tenant}','wecom','{external}','{principal}');
 INSERT INTO access_member(tenant_id,actor_id,status,uses_seat,invited_by)
 VALUES ('{tenant}','{principal}','active',true,'ci-fixture');
 INSERT INTO access_grant(tenant_id,id,actor_id,role_code,resource_id,granted_by,expires_at)
 VALUES ('{tenant}','{author_grant}','{principal}','org_admin',NULL,'ci-fixture',now()+interval '15 minutes');
 INSERT INTO org_login_handoff(handoff_hash,browser_hash,tenant_id,principal_id,connection_id,provider,expires_at)
 VALUES ('{handoff_hash}','{browser_hash}','{tenant}','{principal}','{connection}','wecom','{expiry}'::timestamptz);
END $fixture$;
COMMIT;
"""
