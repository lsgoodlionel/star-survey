-- 管理端浏览器登录的短期一次性交接。授权回调只保存最小主体信息；JWT 仅在后续 POST 交换时签发。
CREATE TABLE org_login_handoff (
    handoff_hash  text        PRIMARY KEY CHECK (handoff_hash ~ '^[0-9a-f]{64}$'),
    browser_hash  text        NOT NULL CHECK (browser_hash ~ '^[0-9a-f]{64}$'),
    tenant_id     uuid        NOT NULL REFERENCES tenant (id),
    principal_id  uuid        NOT NULL,
    connection_id uuid        NOT NULL,
    provider      text        NOT NULL CHECK (provider IN ('wecom', 'dingtalk', 'feishu')),
    created_at    timestamptz NOT NULL DEFAULT now(),
    expires_at    timestamptz NOT NULL,
    consumed_at   timestamptz,
    CONSTRAINT org_login_handoff_principal_fkey
        FOREIGN KEY (tenant_id, principal_id) REFERENCES identity_binding (tenant_id, principal_id),
    CONSTRAINT org_login_handoff_connection_fkey
        FOREIGN KEY (tenant_id, connection_id) REFERENCES org_connection (tenant_id, id)
);
CREATE INDEX org_login_handoff_expiry ON org_login_handoff (tenant_id, expires_at);

ALTER TABLE org_login_handoff ENABLE ROW LEVEL SECURITY;
ALTER TABLE org_login_handoff FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON org_login_handoff
    USING (tenant_id = app_current_tenant()) WITH CHECK (tenant_id = app_current_tenant());

-- 运行期账号可直接插入，但消费与清理只能通过下面两个受租户上下文约束的函数完成。
REVOKE SELECT, UPDATE, DELETE ON org_login_handoff FROM platform_app;

CREATE FUNCTION org_login_handoff_consume(p_tenant uuid, p_hash text, p_browser_hash text, p_now timestamptz)
RETURNS TABLE (browser_hash text, principal_id uuid, connection_id uuid, provider text, expires_at timestamptz)
LANGUAGE sql SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
    UPDATE org_login_handoff AS h
       SET consumed_at = p_now
     WHERE h.tenant_id = p_tenant
       AND h.handoff_hash = p_hash
       AND h.browser_hash = p_browser_hash
       AND h.consumed_at IS NULL
       AND h.expires_at > p_now
       AND p_tenant = app_current_tenant()
    RETURNING h.browser_hash, h.principal_id, h.connection_id, h.provider, h.expires_at
$$;
REVOKE ALL ON FUNCTION org_login_handoff_consume(uuid, text, text, timestamptz) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION org_login_handoff_consume(uuid, text, text, timestamptz) TO platform_app;

CREATE FUNCTION org_login_handoff_purge_stale(p_tenant uuid, p_now timestamptz) RETURNS integer
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
DECLARE
    deleted_rows integer;
BEGIN
    IF p_tenant IS DISTINCT FROM app_current_tenant() THEN
        RAISE EXCEPTION 'tenant scope mismatch' USING ERRCODE = 'insufficient_privilege';
    END IF;
    DELETE FROM org_login_handoff AS h
     WHERE h.tenant_id = p_tenant
       AND (h.expires_at <= p_now OR h.consumed_at <= p_now - interval '1 day');
    GET DIAGNOSTICS deleted_rows = ROW_COUNT;
    RETURN deleted_rows;
END;
$$;
REVOKE ALL ON FUNCTION org_login_handoff_purge_stale(uuid, timestamptz) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION org_login_handoff_purge_stale(uuid, timestamptz) TO platform_app;
