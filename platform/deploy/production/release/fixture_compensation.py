"""Build a compensation transaction for a separately isolated test instance.

Building SQL is not authorization to execute it. The caller must independently
prove exclusive writer quiescence, the exact target and session identities, and
recheck restoration and health. This module does not perform those operations.
"""
import re
import uuid


def _random_uuid(value):
    if not isinstance(value, str):
        raise ValueError('fixture identity must be a canonical random UUID')
    try:
        parsed = uuid.UUID(value)
    except ValueError as exc:
        raise ValueError('fixture identity must be a canonical random UUID') from exc
    if str(parsed) != value or parsed.version != 4 or parsed.variant != uuid.RFC_4122:
        raise ValueError('fixture identity must be a canonical random UUID')
    return value


def build_compensation_sql(*, tenant, owner_plan, fixture_plan, principal, connection,
                           original_sub, fixture_sub, restore_sub, owner_grant,
                           handoff_hash, browser_hash):
    """Return SQL only; its advisory lock does not serialize subscription writers."""
    ids = (tenant, owner_plan, fixture_plan, principal, connection,
           original_sub, fixture_sub, restore_sub, owner_grant)
    for value in ids:
        _random_uuid(value)
    if len(set(ids)) != len(ids):
        raise ValueError('fixture identities must be distinct')
    if any(not isinstance(value, str) or re.fullmatch(r'[0-9a-f]{64}', value) is None
           for value in (handoff_hash, browser_hash)):
        raise ValueError('fixture handoff hashes are invalid')
    scope = "SELECT set_config('app.tenant_id','" + tenant + "',true);"
    return f"""
BEGIN; SET LOCAL ROLE platform_app; {scope}
SELECT pg_advisory_xact_lock(hashtextextended('access.members:{tenant}',0));
DO $$ DECLARE current_id uuid; current_plan uuid; current_kind text; current_end timestamptz;
 original_kind text; original_end timestamptz; handoff_principal uuid; handoff_connection uuid; already_revoked boolean; BEGIN
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
   IF NOT EXISTS (SELECT 1 FROM identity_binding b JOIN org_connection c
       ON c.tenant_id=b.tenant_id AND c.provider=b.provider AND c.corp_id=b.app_id
       WHERE b.tenant_id='{tenant}' AND b.principal_id='{principal}' AND c.id='{connection}') THEN
     RAISE EXCEPTION 'fixture identity binding mismatch';
   END IF;
   SELECT EXISTS (SELECT 1 FROM identity_binding_revocation WHERE tenant_id='{tenant}' AND principal_id='{principal}' AND reinstated_at IS NULL)
      AND EXISTS (SELECT 1 FROM org_connection WHERE tenant_id='{tenant}' AND id='{connection}' AND NOT enabled)
      AND EXISTS (SELECT 1 FROM access_member WHERE tenant_id='{tenant}' AND actor_id='{principal}' AND status='removed')
      INTO already_revoked;
   INSERT INTO identity_binding_revocation(tenant_id,principal_id,reason,revoked_by)
     VALUES ('{tenant}','{principal}','admin','ci-cleanup')
     ON CONFLICT (tenant_id,principal_id) WHERE reinstated_at IS NULL DO NOTHING;
   SELECT principal_id,connection_id INTO handoff_principal,handoff_connection
     FROM org_login_handoff_consume('{tenant}','{handoff_hash}','{browser_hash}',now());
   IF FOUND THEN
     IF handoff_principal IS DISTINCT FROM '{principal}'::uuid OR handoff_connection IS DISTINCT FROM '{connection}'::uuid THEN
       RAISE EXCEPTION 'fixture handoff identity mismatch';
     END IF;
   ELSIF NOT already_revoked THEN
     -- No row is ambiguous: wrong hashes, expiration, absence or prior use.
     -- Do not infer which occurred from the runtime role's limited visibility.
     RAISE EXCEPTION 'fixture handoff state unconfirmed';
   END IF;
   UPDATE org_login_session SET revoked_at=now(),revoke_reason='binding_revoked'
     WHERE tenant_id='{tenant}' AND principal_id='{principal}' AND revoked_at IS NULL;
   DELETE FROM access_grant WHERE tenant_id='{tenant}' AND actor_id='{principal}';
   UPDATE org_connection SET enabled=false,updated_at=now() WHERE tenant_id='{tenant}' AND id='{connection}';
   UPDATE access_member SET status='removed',updated_at=now()
     WHERE tenant_id='{tenant}' AND actor_id='{principal}';
 ELSIF current_id='{fixture_sub}'::uuid OR EXISTS (SELECT 1 FROM access_member WHERE tenant_id='{tenant}' AND actor_id='{principal}') THEN
   RAISE EXCEPTION 'fixture identity is missing';
 END IF;
 IF current_id='{fixture_sub}'::uuid THEN
   INSERT INTO subscription(id,tenant_id,plan_version_id,kind,starts_at,ends_at)
   SELECT '{restore_sub}','{tenant}','{owner_plan}',kind,now(),ends_at FROM subscription WHERE id='{original_sub}';
 END IF;
END $$;
COMMIT;
"""
