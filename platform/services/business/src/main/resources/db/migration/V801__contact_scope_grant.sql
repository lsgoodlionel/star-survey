-- 部门级数据范围（WP-18 切片 18.2，ADR 0017 决定 4）。
--
-- 这不是第二套权限系统：能不能碰通讯录，仍然由 access 模块的 AccessDecisionService 判定
-- （租户级 manage-members 权限）。本表只能**收窄**范围，永远不能放宽：
--   * 有 manage-members、没有范围行 → 全租户联系人（租户通讯录管理员）；
--   * 有 manage-members、有范围行   → 只看得到这些部门及其子孙下的联系人（部门管理员）；
--   * 没有 manage-members           → 什么都看不到（一律 404）。
-- 范围行由已持有租户级 manage-members 的人授予，写审计，可设到期时间，可撤销。
CREATE TABLE contact_scope_grant (
    tenant_id   uuid        NOT NULL,
    id          uuid        NOT NULL,
    actor_id    text        NOT NULL CHECK (length(actor_id) BETWEEN 1 AND 256),
    org_unit_id uuid        NOT NULL,
    granted_by  text        NOT NULL CHECK (length(granted_by) BETWEEN 1 AND 256),
    expires_at  timestamptz,
    created_at  timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, id),
    FOREIGN KEY (tenant_id, org_unit_id) REFERENCES contact_org_unit (tenant_id, id),
    UNIQUE (tenant_id, actor_id, org_unit_id)
);
CREATE INDEX contact_scope_grant_actor ON contact_scope_grant (tenant_id, actor_id);

ALTER TABLE contact_scope_grant ENABLE ROW LEVEL SECURITY;
ALTER TABLE contact_scope_grant FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON contact_scope_grant
    USING (tenant_id = app_current_tenant()) WITH CHECK (tenant_id = app_current_tenant());
