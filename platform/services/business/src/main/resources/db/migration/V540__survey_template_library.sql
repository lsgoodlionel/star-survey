-- 企业模板库：租户私有模板（R19-05，WP-19 切片 19.2）。三张租户表，按行级安全模板隔离。
-- 只引用 V500 的 survey（已发布的旧迁移）；平台（运营）模板在 V541，是控制平面表。
--
-- 模板＝一份**剥掉生产数据**的问卷定义快照。剥离在服务层做并有测试兜底
-- （参与者 token、访问口令哈希、管理员邮箱都不进模板），这里只保证快照本身不可改。
--
-- status 是模板的审核状态机：
--   draft ──> pending ──> published ──> unpublished ──> pending（改版后重新送审）
--               │            └──> unpublished（下架）
--               └──> rejected（须给原因）──> pending（改版后重新送审）
-- 只有 published 的模板对租户可见、可被复制；下架只影响**之后**的复制，
-- 已复制出去的问卷是独立草稿，不受任何影响。
CREATE TABLE survey_template (
    tenant_id         uuid        NOT NULL,
    id                uuid        NOT NULL,
    name              text        NOT NULL CHECK (length(name) BETWEEN 1 AND 200),
    description       text        CHECK (description IS NULL OR length(description) <= 1000),
    status            text        NOT NULL CHECK (status IN
                          ('draft', 'pending', 'published', 'rejected', 'unpublished')),
    -- 最新版本号；published_version 是当前对租户可见的那一版，可以落后于最新版。
    current_version   integer     NOT NULL CHECK (current_version > 0),
    published_version integer     CHECK (published_version IS NULL OR published_version > 0),
    created_by        text        NOT NULL,
    created_at        timestamptz NOT NULL DEFAULT now(),
    updated_at        timestamptz NOT NULL DEFAULT now(),
    reviewed_by       text,
    reviewed_at       timestamptz,
    review_reason     text        CHECK (review_reason IS NULL OR length(review_reason) <= 1000),
    PRIMARY KEY (tenant_id, id),
    UNIQUE (id),
    -- 「可见」与「有在线版本」是同一件事：下架时 published_version 一并清空，
    -- 之后任何复制请求都找不到可复制的版本，不依赖调用方记得多判一个状态。
    CHECK ((status = 'published') = (published_version IS NOT NULL)),
    CHECK (published_version IS NULL OR published_version <= current_version),
    CHECK (status <> 'rejected' OR review_reason IS NOT NULL)
);

CREATE INDEX survey_template_published ON survey_template (tenant_id, updated_at) WHERE status = 'published';
CREATE INDEX survey_template_pending ON survey_template (tenant_id, updated_at) WHERE status = 'pending';

-- 版本快照，只追加、不可改：已复制出去的问卷必须能指回它当时复制的那一版。
CREATE TABLE survey_template_version (
    tenant_id           uuid        NOT NULL,
    template_id         uuid        NOT NULL,
    version_no          integer     NOT NULL CHECK (version_no > 0),
    definition          jsonb       NOT NULL,
    -- 从哪份问卷的哪一版草稿做出来的；问卷后来被删或改动都不影响快照。
    source_survey_id    uuid,
    source_draft_version integer    CHECK (source_draft_version IS NULL OR source_draft_version > 0),
    created_by          text        NOT NULL,
    created_at          timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, template_id, version_no),
    FOREIGN KEY (tenant_id, template_id) REFERENCES survey_template (tenant_id, id)
);

-- 模板的全部动作，只追加：谁在什么时候送审、批准、驳回、下架、复制。
CREATE TABLE survey_template_event (
    tenant_id        uuid        NOT NULL,
    id               bigint      GENERATED ALWAYS AS IDENTITY,
    template_id      uuid        NOT NULL,
    event            text        NOT NULL CHECK (event IN
                         ('created', 'version_added', 'submitted', 'approved', 'rejected', 'unpublished', 'copied')),
    actor            text        NOT NULL,
    version_no       integer,
    reason           text        CHECK (reason IS NULL OR length(reason) <= 1000),
    copied_survey_id uuid,
    occurred_at      timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, id),
    FOREIGN KEY (tenant_id, template_id) REFERENCES survey_template (tenant_id, id)
);
CREATE INDEX survey_template_event_template ON survey_template_event (tenant_id, template_id, id);

-- 最小授权：模板不删除（下架即可）；版本快照与动作历史只追加。
REVOKE DELETE ON survey_template FROM platform_app;
REVOKE UPDATE, DELETE ON survey_template_version FROM platform_app;
REVOKE UPDATE, DELETE ON survey_template_event FROM platform_app;

ALTER TABLE survey_template ENABLE ROW LEVEL SECURITY;
ALTER TABLE survey_template FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON survey_template
    USING (tenant_id = app_current_tenant()) WITH CHECK (tenant_id = app_current_tenant());

ALTER TABLE survey_template_version ENABLE ROW LEVEL SECURITY;
ALTER TABLE survey_template_version FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON survey_template_version
    USING (tenant_id = app_current_tenant()) WITH CHECK (tenant_id = app_current_tenant());

ALTER TABLE survey_template_event ENABLE ROW LEVEL SECURITY;
ALTER TABLE survey_template_event FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON survey_template_event
    USING (tenant_id = app_current_tenant()) WITH CHECK (tenant_id = app_current_tenant());
