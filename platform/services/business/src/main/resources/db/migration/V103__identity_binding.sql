-- 身份绑定（身份车道，最小模型）：外部身份三元组 (提供方, 企业/应用标识, 外部标识) -> 平台主体。
-- 三元组全局唯一；只按外部标识唯一是错误的——openid 只在一个应用内唯一（openid 作用域陷阱）。

CREATE TABLE identity_binding (
    principal_id  uuid        PRIMARY KEY,
    tenant_id     uuid        NOT NULL REFERENCES tenant (id),
    provider      text        NOT NULL CHECK (provider ~ '^[a-z][a-z0-9_]{1,31}$'),
    app_id        text        NOT NULL CHECK (length(app_id) BETWEEN 1 AND 128),
    external_id   text        NOT NULL CHECK (length(external_id) BETWEEN 1 AND 256),
    created_at    timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT identity_binding_identity_key UNIQUE (provider, app_id, external_id)
);

-- 绑定一经建立不改写、不删除（解绑与迁移留待有明确需求时单独设计），运行期账号只有查询与新增。
REVOKE UPDATE, DELETE ON identity_binding FROM platform_app;

ALTER TABLE identity_binding ENABLE ROW LEVEL SECURITY;
ALTER TABLE identity_binding FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON identity_binding
    USING (tenant_id = app_current_tenant()) WITH CHECK (tenant_id = app_current_tenant());
