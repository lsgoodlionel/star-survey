-- 通讯录：名单、部门树、联系人、标签与身份关联（WP-18 切片 18.1/18.2，ADR 0017）。
-- contacts 车道号段 V800–V899，不依赖其他车道的迁移顺序，也不对其他模块的表建外键。
--
-- 三类业务身份（staff 平台员工账号 / org_member 组织成员 / respondent 答卷联系人）各自成行，
-- 永不隐式合并：去重只在名单内、同一类身份内进行，跨类只能显式建立 contact_link 关联。

-- 联系人名单：一次导入的目标，同时固定这份名单的身份类别与去重键。
-- 去重键建名单时由租户选定，之后不可改：改键等于重新划分已有行的去重分组，只能新建名单再导入。
CREATE TABLE contact_list (
    tenant_id   uuid        NOT NULL,
    id          uuid        NOT NULL,
    name        text        NOT NULL CHECK (length(name) BETWEEN 1 AND 200),
    kind        text        NOT NULL CHECK (kind IN ('staff', 'org_member', 'respondent')),
    dedupe_key  text        NOT NULL CHECK (dedupe_key IN ('email', 'phone', 'employee_id', 'external_id')),
    created_by  text        NOT NULL CHECK (length(created_by) BETWEEN 1 AND 256),
    created_at  timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, id),
    -- 供 contact 的复合外键引用：联系人的身份类别必须与所属名单一致。
    UNIQUE (tenant_id, id, kind)
);
CREATE UNIQUE INDEX contact_list_name ON contact_list (tenant_id, lower(name));

-- 部门树：每租户一棵（可以有多个根）。名称之外不存任何个人信息。
CREATE TABLE contact_org_unit (
    tenant_id    uuid        NOT NULL,
    id           uuid        NOT NULL,
    parent_id    uuid,
    name         text        NOT NULL CHECK (length(name) BETWEEN 1 AND 200),
    -- 组织同步来源的部门号（企业微信 / 钉钉 / 飞书），只是外部标识，不是个人信息。
    external_ref text        CHECK (length(external_ref) BETWEEN 1 AND 128),
    created_by   text        NOT NULL CHECK (length(created_by) BETWEEN 1 AND 256),
    created_at   timestamptz NOT NULL DEFAULT now(),
    updated_at   timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, id),
    FOREIGN KEY (tenant_id, parent_id) REFERENCES contact_org_unit (tenant_id, id),
    CHECK (parent_id IS DISTINCT FROM id)
);
CREATE INDEX contact_org_unit_parent ON contact_org_unit (tenant_id, parent_id);
CREATE UNIQUE INDEX contact_org_unit_sibling_name ON contact_org_unit (tenant_id, parent_id, lower(name))
    WHERE parent_id IS NOT NULL;
CREATE UNIQUE INDEX contact_org_unit_root_name ON contact_org_unit (tenant_id, lower(name))
    WHERE parent_id IS NULL;

-- 部门树的数据库兜底：即使有代码绕过服务层直接改 parent_id，也不能成环、不能改租户 / id。
-- 跨租户的父节点已由复合外键挡住，行级安全让别的租户的行不可见。
CREATE FUNCTION contact_org_unit_guard() RETURNS trigger
    LANGUAGE plpgsql
AS $$
DECLARE
    ancestor uuid;
    steps    integer := 0;
BEGIN
    IF TG_OP = 'UPDATE' AND (NEW.tenant_id <> OLD.tenant_id OR NEW.id <> OLD.id) THEN
        RAISE EXCEPTION 'contact_org_unit: tenant_id and id are immutable' USING ERRCODE = 'check_violation';
    END IF;
    IF NEW.parent_id IS NULL OR (TG_OP = 'UPDATE' AND NEW.parent_id IS NOT DISTINCT FROM OLD.parent_id) THEN
        RETURN NEW;
    END IF;
    -- 沿新父节点向上走：遇到自己即成环。新插入的节点还没有子孙，只需检查更新。
    IF TG_OP = 'UPDATE' THEN
        ancestor := NEW.parent_id;
        WHILE ancestor IS NOT NULL LOOP
            IF ancestor = NEW.id THEN
                RAISE EXCEPTION 'contact_org_unit: moving % under its own descendant', NEW.id
                    USING ERRCODE = 'check_violation';
            END IF;
            steps := steps + 1;
            IF steps > 10000 THEN
                RAISE EXCEPTION 'contact_org_unit: ancestor chain too deep' USING ERRCODE = 'check_violation';
            END IF;
            SELECT parent_id INTO ancestor FROM contact_org_unit WHERE tenant_id = NEW.tenant_id AND id = ancestor;
        END LOOP;
    END IF;
    RETURN NEW;
END
$$;

CREATE TRIGGER contact_org_unit_guard
    BEFORE INSERT OR UPDATE ON contact_org_unit
    FOR EACH ROW EXECUTE FUNCTION contact_org_unit_guard();

-- 联系人。含个人信息，任何日志都不得输出这些列（ADR 0017 决定 6）。
-- dedupe_value 是按所属名单的去重键算出的规范化值（邮箱小写、手机去分隔符、
-- 外部身份为 provider|app_id|external_id）；唯一索引按名单生效，这就是"去重"的全部含义。
CREATE TABLE contact (
    tenant_id     uuid        NOT NULL,
    id            uuid        NOT NULL,
    kind          text        NOT NULL CHECK (kind IN ('staff', 'org_member', 'respondent')),
    list_id       uuid,
    org_unit_id   uuid,
    display_name  text        CHECK (length(display_name) BETWEEN 1 AND 200),
    email         text        CHECK (length(email) BETWEEN 3 AND 320),
    phone         text        CHECK (phone ~ '^\+?[0-9]{5,20}$'),
    employee_id   text        CHECK (employee_id ~ '^[A-Za-z0-9._-]{1,64}$'),
    provider      text        CHECK (provider ~ '^[a-z][a-z0-9_]{1,31}$'),
    app_id        text        CHECK (length(app_id) BETWEEN 1 AND 128),
    external_id   text        CHECK (length(external_id) BETWEEN 1 AND 256),
    dedupe_value  text        CHECK (length(dedupe_value) BETWEEN 1 AND 512),
    source        text        NOT NULL CHECK (source IN ('manual', 'import', 'org_sync', 'api', 'response')),
    status        text        NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'disabled')),
    -- 组织成员联系人对应的 identity_binding 主体（同租户）。跨模块不建外键（CONVENTIONS.md）。
    principal_id  uuid,
    -- 员工联系人对应的平台账号标识（access_member.actor_id）；同样不跨模块建外键。
    actor_id      text        CHECK (length(actor_id) BETWEEN 1 AND 256),
    created_by    text        NOT NULL CHECK (length(created_by) BETWEEN 1 AND 256),
    created_at    timestamptz NOT NULL DEFAULT now(),
    updated_at    timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, id),
    FOREIGN KEY (tenant_id, list_id, kind) REFERENCES contact_list (tenant_id, id, kind),
    FOREIGN KEY (tenant_id, org_unit_id) REFERENCES contact_org_unit (tenant_id, id),
    -- 外部身份三件套要么都有要么都没有：只有 (provider, app_id, external_id) 整体才是一个身份。
    CHECK (num_nonnulls(provider, app_id, external_id) IN (0, 3)),
    -- 名单内的联系人必须有去重值，否则无法判重。
    CHECK ((list_id IS NULL) OR (dedupe_value IS NOT NULL)),
    -- 平台账号只挂在员工联系人上，组织主体只挂在组织成员联系人上：三类身份的归属不串。
    CHECK (actor_id IS NULL OR kind = 'staff'),
    CHECK (principal_id IS NULL OR kind = 'org_member')
);

-- 名单内去重：同一名单内同一规范化值只有一行。不同名单、不同类别互不影响（永不隐式合并）。
CREATE UNIQUE INDEX contact_dedupe ON contact (tenant_id, list_id, dedupe_value)
    WHERE list_id IS NOT NULL;
-- 不属于任何名单的目录联系人（组织同步 / API 沉淀）：按 (类别, provider, app_id, external_id) 唯一。
-- app_id 进入键，所以"同一 openid 出现在两个应用下"必然是两行。
CREATE UNIQUE INDEX contact_directory_identity
    ON contact (tenant_id, kind, provider, app_id, external_id)
    WHERE list_id IS NULL AND external_id IS NOT NULL;
CREATE INDEX contact_unit ON contact (tenant_id, org_unit_id);
CREATE INDEX contact_list_rows ON contact (tenant_id, list_id);

-- 标签：每租户自己的一套，名称不区分大小写唯一。
CREATE TABLE contact_tag (
    tenant_id  uuid        NOT NULL,
    id         uuid        NOT NULL,
    name       text        NOT NULL CHECK (length(name) BETWEEN 1 AND 64),
    created_by text        NOT NULL CHECK (length(created_by) BETWEEN 1 AND 256),
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, id)
);
CREATE UNIQUE INDEX contact_tag_name ON contact_tag (tenant_id, lower(name));

CREATE TABLE contact_tag_assignment (
    tenant_id   uuid        NOT NULL,
    contact_id  uuid        NOT NULL,
    tag_id      uuid        NOT NULL,
    assigned_by text        NOT NULL CHECK (length(assigned_by) BETWEEN 1 AND 256),
    assigned_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, contact_id, tag_id),
    FOREIGN KEY (tenant_id, contact_id) REFERENCES contact (tenant_id, id),
    FOREIGN KEY (tenant_id, tag_id) REFERENCES contact_tag (tenant_id, id)
);
CREATE INDEX contact_tag_assignment_tag ON contact_tag_assignment (tenant_id, tag_id);

-- 身份关联：显式、留痕、可撤销，但**不合并**——两行联系人各自保留 id、部门、标签与生命周期。
-- 无向关系，按 id 大小规范化存储，避免同一对出现两行。
CREATE TABLE contact_link (
    tenant_id        uuid        NOT NULL,
    id               uuid        NOT NULL,
    left_contact_id  uuid        NOT NULL,
    right_contact_id uuid        NOT NULL,
    reason           text        NOT NULL CHECK (length(reason) BETWEEN 1 AND 200),
    linked_by        text        NOT NULL CHECK (length(linked_by) BETWEEN 1 AND 256),
    linked_at        timestamptz NOT NULL DEFAULT now(),
    unlinked_by      text        CHECK (length(unlinked_by) BETWEEN 1 AND 256),
    unlinked_at      timestamptz,
    PRIMARY KEY (tenant_id, id),
    FOREIGN KEY (tenant_id, left_contact_id) REFERENCES contact (tenant_id, id),
    FOREIGN KEY (tenant_id, right_contact_id) REFERENCES contact (tenant_id, id),
    CHECK (left_contact_id < right_contact_id),
    CHECK ((unlinked_at IS NULL) = (unlinked_by IS NULL)),
    CHECK (unlinked_at IS NULL OR unlinked_at >= linked_at)
);
CREATE UNIQUE INDEX contact_link_open ON contact_link (tenant_id, left_contact_id, right_contact_id)
    WHERE unlinked_at IS NULL;

-- 最小授权：名单、部门、联系人、标签可改不可删（留痕）；关联只追加，撤销走两列 UPDATE。
REVOKE DELETE ON contact_list, contact_org_unit, contact, contact_tag, contact_link FROM platform_app;
REVOKE UPDATE ON contact_link FROM platform_app;
GRANT UPDATE (unlinked_by, unlinked_at) ON contact_link TO platform_app;

ALTER TABLE contact_list ENABLE ROW LEVEL SECURITY;
ALTER TABLE contact_list FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON contact_list
    USING (tenant_id = app_current_tenant()) WITH CHECK (tenant_id = app_current_tenant());

ALTER TABLE contact_org_unit ENABLE ROW LEVEL SECURITY;
ALTER TABLE contact_org_unit FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON contact_org_unit
    USING (tenant_id = app_current_tenant()) WITH CHECK (tenant_id = app_current_tenant());

ALTER TABLE contact ENABLE ROW LEVEL SECURITY;
ALTER TABLE contact FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON contact
    USING (tenant_id = app_current_tenant()) WITH CHECK (tenant_id = app_current_tenant());

ALTER TABLE contact_tag ENABLE ROW LEVEL SECURITY;
ALTER TABLE contact_tag FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON contact_tag
    USING (tenant_id = app_current_tenant()) WITH CHECK (tenant_id = app_current_tenant());

ALTER TABLE contact_tag_assignment ENABLE ROW LEVEL SECURITY;
ALTER TABLE contact_tag_assignment FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON contact_tag_assignment
    USING (tenant_id = app_current_tenant()) WITH CHECK (tenant_id = app_current_tenant());

ALTER TABLE contact_link ENABLE ROW LEVEL SECURITY;
ALTER TABLE contact_link FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON contact_link
    USING (tenant_id = app_current_tenant()) WITH CHECK (tenant_id = app_current_tenant());
