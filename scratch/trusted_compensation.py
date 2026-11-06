"""CI-only proof of exact handoff state on an independently isolated session.

The caller must already have exclusive control of the database and a pinned
privileged session. This is not safe as a general-purpose product API.
"""
from fixture_compensation import build_compensation_sql


def build_trusted_compensation(*, tenant, principal, connection, handoff_hash, browser_hash, **rest):
    sql = build_compensation_sql(tenant=tenant, principal=principal, connection=connection,
                                 handoff_hash=handoff_hash, browser_hash=browser_hash, **rest)
    begin = 'BEGIN; SET LOCAL ROLE platform_app;'
    guard = 'ELSIF NOT already_revoked THEN'
    if sql.count(begin) != 1 or sql.count(guard) != 1:
        raise RuntimeError('unexpected compensation SQL contract')
    # Both hashes and every identity are already format-validated by builder.
    # Lock and validate the exact row before switching to the runtime role.
    prelude = f"""BEGIN;
DO $fixture_verify$ BEGIN
  PERFORM 1 FROM org_login_handoff
    WHERE handoff_hash='{handoff_hash}' AND browser_hash='{browser_hash}'
      AND tenant_id='{tenant}' AND principal_id='{principal}' AND connection_id='{connection}'
      AND (consumed_at IS NOT NULL OR expires_at <= now()) FOR UPDATE;
  IF NOT FOUND THEN RAISE EXCEPTION 'trusted handoff state not established'; END IF;
END $fixture_verify$;
SET LOCAL ROLE platform_app;"""
    return sql.replace(begin, prelude, 1).replace(guard, 'ELSIF false THEN', 1)
