-- 免登流程的一次性 state 与平台登录会话。
--
-- org_login_state：发起登录时生成，绑定租户、连接与浏览器（browser_hash 是浏览器 Cookie 里随机值的 SHA-256，
-- 明文只在浏览器里）。回调时按"未消费、未过期、Cookie 相符"原子消费一次，重放、换租户、过期一律拒绝。
CREATE TABLE org_login_state (
    state         text        PRIMARY KEY CHECK (state ~ '^[A-Za-z0-9]{32,128}$'),
    tenant_id     uuid        NOT NULL,
    connection_id uuid        NOT NULL REFERENCES org_connection (id),
    browser_hash  text        NOT NULL CHECK (browser_hash ~ '^[0-9a-f]{64}$'),
    created_at    timestamptz NOT NULL DEFAULT now(),
    expires_at    timestamptz NOT NULL,
    consumed_at   timestamptz
);
CREATE INDEX org_login_state_expiry ON org_login_state (tenant_id, expires_at);

ALTER TABLE org_login_state ENABLE ROW LEVEL SECURITY;
ALTER TABLE org_login_state FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON org_login_state
    USING (tenant_id = app_current_tenant()) WITH CHECK (tenant_id = app_current_tenant());

-- org_login_session：每次免登签发的平台令牌对应一行（令牌 sid 声明 = id）。
-- 每个请求都核对会话仍有效，撤销（离职、登出）立即生效，不必等令牌过期。
CREATE TABLE org_login_session (
    id             uuid        PRIMARY KEY,
    tenant_id      uuid        NOT NULL,
    principal_id   uuid        NOT NULL REFERENCES identity_binding (principal_id),
    connection_id  uuid        NOT NULL REFERENCES org_connection (id),
    issued_at      timestamptz NOT NULL DEFAULT now(),
    expires_at     timestamptz NOT NULL,
    revoked_at     timestamptz,
    revoke_reason  text        CHECK (revoke_reason IN ('departed', 'logout', 'binding_revoked')),
    CHECK ((revoked_at IS NULL) = (revoke_reason IS NULL))
);
CREATE INDEX org_login_session_principal ON org_login_session (tenant_id, principal_id);

-- 会话只会被撤销，不会被删除。
REVOKE DELETE ON org_login_session FROM platform_app;

ALTER TABLE org_login_session ENABLE ROW LEVEL SECURITY;
ALTER TABLE org_login_session FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON org_login_session
    USING (tenant_id = app_current_tenant()) WITH CHECK (tenant_id = app_current_tenant());
