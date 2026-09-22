-- 身份绑定撤销（离职后立即失权）。identity_binding 本身不可改写（V103），撤销单独记一行：
-- 有撤销记录的绑定不能再免登，其会话同时被撤销。只追加：撤销不可被运行期账号抹掉。
CREATE TABLE identity_binding_revocation (
    principal_id uuid        PRIMARY KEY REFERENCES identity_binding (principal_id),
    tenant_id    uuid        NOT NULL,
    reason       text        NOT NULL CHECK (reason IN ('departed', 'disabled', 'admin')),
    revoked_by   text        NOT NULL,
    revoked_at   timestamptz NOT NULL DEFAULT now()
);

REVOKE UPDATE, DELETE ON identity_binding_revocation FROM platform_app;

ALTER TABLE identity_binding_revocation ENABLE ROW LEVEL SECURITY;
ALTER TABLE identity_binding_revocation FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON identity_binding_revocation
    USING (tenant_id = app_current_tenant()) WITH CHECK (tenant_id = app_current_tenant());
