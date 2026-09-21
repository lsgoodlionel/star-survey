-- 平台基线：租户登记与行级安全的公共设施。
-- 本迁移由 platform_owner 执行；应用以 platform_app 运行，后者不拥有表、没有 BYPASSRLS。

-- 当前事务的租户。未设置时返回 NULL（set_config 失效后为空串，这里统一折叠为 NULL），
-- 与任何 tenant_id 比较都不成立，因此"没有租户上下文"等于"什么都看不到、什么都写不进"。
CREATE FUNCTION app_current_tenant() RETURNS uuid
    LANGUAGE sql STABLE
AS $$ SELECT NULLIF(current_setting('app.tenant_id', true), '')::uuid $$;

-- 之后由 platform_owner 创建的表，默认向运行期账号授予增删改查；需要更严的表在建表后单独收回。
ALTER DEFAULT PRIVILEGES FOR ROLE platform_owner IN SCHEMA public
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO platform_app;
ALTER DEFAULT PRIVILEGES FOR ROLE platform_owner IN SCHEMA public
    GRANT USAGE, SELECT ON SEQUENCES TO platform_app;
GRANT USAGE ON SCHEMA public TO platform_app;
GRANT EXECUTE ON FUNCTION app_current_tenant() TO platform_app;

-- 租户登记属于控制平面，不做行级隔离；运行期账号只读。
CREATE TABLE tenant (
    id          uuid        PRIMARY KEY,
    code        text        NOT NULL UNIQUE,
    name        text        NOT NULL,
    status      text        NOT NULL CHECK (status IN ('active', 'suspended', 'closed')),
    created_at  timestamptz NOT NULL DEFAULT now()
);
REVOKE INSERT, UPDATE, DELETE ON tenant FROM platform_app;

-- 审计日志：按租户隔离，只追加。
CREATE TABLE audit_log (
    id          bigserial   PRIMARY KEY,
    tenant_id   uuid        NOT NULL,
    actor_id    text        NOT NULL,
    action      text        NOT NULL,
    resource    text        NOT NULL,
    trace_id    text,
    occurred_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX audit_log_tenant_time ON audit_log (tenant_id, occurred_at);
REVOKE UPDATE, DELETE ON audit_log FROM platform_app;

-- 行级安全模板：每张租户表都按这四行建立。FORCE 让策略对表所有者同样生效。
ALTER TABLE audit_log ENABLE ROW LEVEL SECURITY;
ALTER TABLE audit_log FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON audit_log
    USING (tenant_id = app_current_tenant())
    WITH CHECK (tenant_id = app_current_tenant());
