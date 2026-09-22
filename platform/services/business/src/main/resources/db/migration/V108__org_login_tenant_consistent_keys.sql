-- 组织免登各表的租户一致性（安全审查结论）。
-- 行级安全只校验子行自身的 tenant_id，而外键检查绕过行级安全：只按 id 引用时，
-- 租户 B 的行可以指向租户 A 的连接或主体。改为带 tenant_id 的复合外键，并补上指向 tenant 的外键。

ALTER TABLE org_connection
    ADD CONSTRAINT org_connection_tenant_id_key UNIQUE (tenant_id, id);
ALTER TABLE identity_binding
    ADD CONSTRAINT identity_binding_tenant_principal_key UNIQUE (tenant_id, principal_id);

ALTER TABLE org_login_state
    DROP CONSTRAINT org_login_state_connection_id_fkey,
    ADD CONSTRAINT org_login_state_tenant_fkey FOREIGN KEY (tenant_id) REFERENCES tenant (id),
    ADD CONSTRAINT org_login_state_connection_fkey
        FOREIGN KEY (tenant_id, connection_id) REFERENCES org_connection (tenant_id, id);

ALTER TABLE org_login_session
    DROP CONSTRAINT org_login_session_principal_id_fkey,
    DROP CONSTRAINT org_login_session_connection_id_fkey,
    ADD CONSTRAINT org_login_session_tenant_fkey FOREIGN KEY (tenant_id) REFERENCES tenant (id),
    ADD CONSTRAINT org_login_session_principal_fkey
        FOREIGN KEY (tenant_id, principal_id) REFERENCES identity_binding (tenant_id, principal_id),
    ADD CONSTRAINT org_login_session_connection_fkey
        FOREIGN KEY (tenant_id, connection_id) REFERENCES org_connection (tenant_id, id);

ALTER TABLE identity_binding_revocation
    DROP CONSTRAINT identity_binding_revocation_principal_id_fkey,
    ADD CONSTRAINT identity_binding_revocation_tenant_fkey FOREIGN KEY (tenant_id) REFERENCES tenant (id),
    ADD CONSTRAINT identity_binding_revocation_principal_fkey
        FOREIGN KEY (tenant_id, principal_id) REFERENCES identity_binding (tenant_id, principal_id);
