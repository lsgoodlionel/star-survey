#!/usr/bin/env python3
"""Isolated real-stack personnel identity and tenant status browser fixture."""
import base64
import hashlib
import hmac
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid

from admin_web_gate import write_private


def sql(statement):
    result = subprocess.run(['docker', 'exec', '-i', os.environ['ADMIN_WEB_PLATFORM_DB_CONTAINER'],
                             'psql', '-U', 'postgres', '-d', 'platform', '-v', 'ON_ERROR_STOP=1', '-q'],
                            input=statement, text=True, capture_output=True, check=False)
    if result.returncode:
        raise RuntimeError('isolated personnel fixture database operation failed')


def seed():
    identity_tenant = str(uuid.UUID(json.loads(Path(os.environ['ADMIN_WEB_METADATA_FILE']).read_text())['tenantId']))
    principal, sid, connection, target = (str(uuid.uuid4()) for _ in range(4))
    code = 'personnel-' + uuid.uuid4().hex[:20]
    sql(f"""
BEGIN;
INSERT INTO tenant(id,code,name,status) VALUES ('{target}','{code}','人员状态验收租户','provisioning');
UPDATE tenant SET status='active' WHERE id='{target}';
INSERT INTO identity_binding(principal_id,tenant_id,provider,app_id,external_id)
  VALUES ('{principal}','{identity_tenant}','wecom','personnel-e2e','{principal}');
INSERT INTO access_member(tenant_id,actor_id,status,uses_seat,invited_by)
  VALUES ('{identity_tenant}','{principal}','active',true,'isolated-test');
INSERT INTO org_connection(id,tenant_id,provider,corp_id,app_id,secret_ref,unknown_user_policy,enabled,created_by)
  VALUES ('{connection}','{identity_tenant}','wecom','personnel-e2e','app','UNUSED_TEST_REF','jit_staff',true,'isolated-test');
INSERT INTO org_login_session(id,tenant_id,principal_id,connection_id,expires_at)
  VALUES ('{sid}','{identity_tenant}','{principal}','{connection}',now()+interval '10 minutes');
SELECT platform_admin_bootstrap('{identity_tenant}','{principal}','personnel-e2e','isolated test');
COMMIT;
""")
    now = int(time.time())
    enc = lambda raw: base64.urlsafe_b64encode(raw).rstrip(b'=').decode('ascii')
    header = enc(b'{"alg":"HS256","typ":"JWT"}')
    payload = enc(json.dumps({'sub': principal, 'tenant_id': identity_tenant, 'roles': [],
                              'iss': 'mjy-platform', 'sid': sid, 'iat': now, 'exp': now + 600},
                             separators=(',', ':')).encode())
    message = (header + '.' + payload).encode('ascii')
    key = os.environ['PLATFORM_JWT_HMAC_SECRET'].encode()
    signature = enc(hmac.new(key, message, hashlib.sha256).digest())
    write_private(Path(os.environ['ADMIN_WEB_PERSONNEL_JWT_FILE']), header + '.' + payload + '.' + signature)
    Path(os.environ['ADMIN_WEB_PERSONNEL_METADATA_FILE']).write_text(json.dumps(
        {'identityTenant': identity_tenant, 'principal': principal, 'targetId': target}), encoding='utf-8')


def verify_and_revoke():
    metadata = json.loads(Path(os.environ['ADMIN_WEB_PERSONNEL_METADATA_FILE']).read_text())
    tenant, principal, target = (str(uuid.UUID(metadata[key])) for key in ('identityTenant', 'principal', 'targetId'))
    sql(f"""
BEGIN;
DO $$ BEGIN
  IF (SELECT count(*) FROM audit_log WHERE tenant_id='{target}' AND actor_id='{principal}'
      AND action IN ('tenant.status:active->suspended','tenant.status:suspended->closed')) <> 2 THEN
    RAISE EXCEPTION 'personnel status audit mismatch';
  END IF;
  IF NOT EXISTS (SELECT 1 FROM tenant WHERE id='{target}' AND status='closed') THEN
    RAISE EXCEPTION 'tenant terminal state mismatch';
  END IF;
END $$;
UPDATE access_member SET status='removed' WHERE tenant_id='{tenant}' AND actor_id='{principal}';
DO $$ BEGIN
  IF EXISTS (SELECT 1 FROM platform_admin_grant WHERE identity_tenant='{tenant}' AND subject='{principal}' AND active) THEN
    RAISE EXCEPTION 'personnel grant was not invalidated';
  END IF;
END $$;
COMMIT;
""")


if __name__ == '__main__':
    if sys.argv[1:] == ['seed']:
        seed()
    elif sys.argv[1:] == ['verify-and-revoke']:
        verify_and_revoke()
    else:
        raise SystemExit('expected seed or verify-and-revoke')
