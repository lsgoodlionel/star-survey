-- 租户内的资源树、成员、授权与访问设置。每张表都按行级安全模板隔离。
-- 复合外键都带 tenant_id：即使代码把别的租户的 id 传进来，也引用不到别的租户的行。

-- 资源树：项目 > 文件夹（可嵌套）> 问卷。授权落在节点上并向下继承。
-- 节点只能挂在已存在的父节点下、不支持移动，因此不会成环。
CREATE TABLE access_resource (
    tenant_id  uuid        NOT NULL,
    id         uuid        NOT NULL,
    kind       text        NOT NULL CHECK (kind IN ('project', 'folder', 'survey')),
    parent_id  uuid,
    name       text        NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, id),
    FOREIGN KEY (tenant_id, parent_id) REFERENCES access_resource (tenant_id, id),
    CHECK ((kind = 'project') = (parent_id IS NULL))
);
CREATE INDEX access_resource_parent ON access_resource (tenant_id, parent_id);

-- 成员：占用席位的成员数不得超过额度模块给出的席位上限（邀请中也占席位）。
CREATE TABLE access_member (
    tenant_id  uuid        NOT NULL,
    actor_id   text        NOT NULL,
    status     text        NOT NULL CHECK (status IN ('invited', 'active', 'removed')),
    uses_seat  boolean     NOT NULL,
    invited_by text        NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, actor_id)
);

-- 授权：成员 + 角色 + 范围。resource_id 为空表示整个租户。
CREATE TABLE access_grant (
    tenant_id   uuid        NOT NULL,
    id          uuid        NOT NULL,
    actor_id    text        NOT NULL,
    role_code   text        NOT NULL REFERENCES access_role (code),
    resource_id uuid,
    granted_by  text        NOT NULL,
    expires_at  timestamptz,
    created_at  timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, id),
    FOREIGN KEY (tenant_id, actor_id) REFERENCES access_member (tenant_id, actor_id),
    FOREIGN KEY (tenant_id, resource_id) REFERENCES access_resource (tenant_id, id),
    UNIQUE NULLS NOT DISTINCT (tenant_id, actor_id, role_code, resource_id)
);
CREATE INDEX access_grant_actor ON access_grant (tenant_id, actor_id);

-- 租户级访问设置。没有行时按"发布需要审核"处理（失败即保守）。
CREATE TABLE access_tenant_settings (
    tenant_id                 uuid        PRIMARY KEY,
    publish_approval_required boolean     NOT NULL,
    updated_by                text        NOT NULL,
    updated_at                timestamptz NOT NULL DEFAULT now()
);

ALTER TABLE access_resource ENABLE ROW LEVEL SECURITY;
ALTER TABLE access_resource FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON access_resource
    USING (tenant_id = app_current_tenant()) WITH CHECK (tenant_id = app_current_tenant());

ALTER TABLE access_member ENABLE ROW LEVEL SECURITY;
ALTER TABLE access_member FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON access_member
    USING (tenant_id = app_current_tenant()) WITH CHECK (tenant_id = app_current_tenant());

ALTER TABLE access_grant ENABLE ROW LEVEL SECURITY;
ALTER TABLE access_grant FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON access_grant
    USING (tenant_id = app_current_tenant()) WITH CHECK (tenant_id = app_current_tenant());

ALTER TABLE access_tenant_settings ENABLE ROW LEVEL SECURITY;
ALTER TABLE access_tenant_settings FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON access_tenant_settings
    USING (tenant_id = app_current_tenant()) WITH CHECK (tenant_id = app_current_tenant());
