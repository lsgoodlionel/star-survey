-- 问卷定义与发布（survey 车道）。四张表都是租户表，按行级安全模板隔离。
-- 本车道不对其他模块的表建外键（资源树、引擎实例、路由都经各模块的公开服务方法维护），
-- 表内的复合外键一律带 tenant_id：即使代码传错了 id，也引用不到别的租户的行。

-- 问卷：公开 UUID（id）一经生成不变；草稿就地保存，draft_version 做乐观锁。
-- status 是发布状态机：
--   draft ──> publishing ──> published（终态；重新发布见 ADR 0009 已知限制 2，尚未实现）
--                  │  ├──> publish_failed ──> publishing（新 requestId 重试）
--                  │  └──> pending_reconciliation ──> publishing（同一 requestId 重试，靠网关幂等）
-- current_request_id / publishing_started_at 记录最近一次发布尝试；只有持行锁者可改。
CREATE TABLE survey (
    tenant_id             uuid        NOT NULL,
    id                    uuid        NOT NULL,
    title                 text        NOT NULL,
    status                text        NOT NULL DEFAULT 'draft' CHECK (status IN
                              ('draft', 'publishing', 'published', 'publish_failed', 'pending_reconciliation')),
    draft_definition      jsonb       NOT NULL,
    draft_version         integer     NOT NULL CHECK (draft_version > 0),
    current_request_id    uuid,
    publishing_started_at timestamptz,
    published_version     integer,
    created_by            text        NOT NULL,
    created_at            timestamptz NOT NULL DEFAULT now(),
    updated_at            timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, id),
    -- 公开 UUID 全局唯一（路由表以它为主键）。
    UNIQUE (id),
    CHECK ((status = 'publishing') = (publishing_started_at IS NOT NULL)),
    CHECK ((status = 'draft') = (current_request_id IS NULL))
);

-- 发布尝试：每个 requestId 一行，先于网关调用落库。同一 requestId 的重试复用本行
-- （同一实例、同一份定义快照），因此"待核对"时发出的是与首次完全相同的请求。
CREATE TABLE survey_publish_attempt (
    tenant_id          uuid        NOT NULL,
    request_id         uuid        NOT NULL,
    survey_id          uuid        NOT NULL,
    draft_version      integer     NOT NULL,
    engine_instance_id text        NOT NULL,
    definition         jsonb       NOT NULL,
    outcome            text        NOT NULL CHECK (outcome IN ('in_flight', 'published', 'failed', 'unknown')),
    gateway_status     integer,
    failed_stage       text,
    failures           jsonb       NOT NULL DEFAULT '[]'::jsonb,
    -- 网关回滚也失败时引擎里残留的 sid，需要人工清理。
    orphan_engine_sid  integer,
    tries              integer     NOT NULL DEFAULT 1 CHECK (tries > 0),
    requested_by       text        NOT NULL,
    created_at         timestamptz NOT NULL DEFAULT now(),
    completed_at       timestamptz,
    PRIMARY KEY (tenant_id, request_id),
    UNIQUE (request_id),
    FOREIGN KEY (tenant_id, survey_id) REFERENCES survey (tenant_id, id)
);
CREATE INDEX survey_publish_attempt_survey ON survey_publish_attempt (tenant_id, survey_id, created_at);

-- 已发布版本：一经写入不可修改、不可删除（运行期账号没有 UPDATE/DELETE）。
-- definition 是当时发给网关的快照，binding 是网关返回的绑定记录原文。
CREATE TABLE survey_published_version (
    tenant_id           uuid        NOT NULL,
    survey_id           uuid        NOT NULL,
    version_no          integer     NOT NULL CHECK (version_no > 0),
    request_id          uuid        NOT NULL,
    draft_version       integer     NOT NULL,
    engine_instance_id  text        NOT NULL,
    engine_sid          integer     NOT NULL CHECK (engine_sid > 0),
    definition          jsonb       NOT NULL,
    compiler_version    text        NOT NULL,
    fingerprint_version text        NOT NULL,
    fingerprint         text        NOT NULL,
    language            text        NOT NULL,
    engine_published_at text        NOT NULL,
    binding             jsonb       NOT NULL,
    published_by        text        NOT NULL,
    published_at        timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, survey_id, version_no),
    UNIQUE (tenant_id, request_id),
    FOREIGN KEY (tenant_id, survey_id) REFERENCES survey (tenant_id, id),
    FOREIGN KEY (tenant_id, request_id) REFERENCES survey_publish_attempt (tenant_id, request_id)
);

-- 三段映射：平台题目 UUID → 题目代码 → 引擎答卷列名（每列一行）。同样只追加。
CREATE TABLE survey_question_binding (
    tenant_id     uuid    NOT NULL,
    survey_id     uuid    NOT NULL,
    version_no    integer NOT NULL,
    ordinal       integer NOT NULL,
    question_uuid uuid    NOT NULL,
    question_code text    NOT NULL,
    question_type text    NOT NULL,
    fieldname     text    NOT NULL,
    aid           text    NOT NULL,
    scale         integer NOT NULL,
    PRIMARY KEY (tenant_id, survey_id, version_no, ordinal),
    UNIQUE (tenant_id, survey_id, version_no, fieldname),
    FOREIGN KEY (tenant_id, survey_id, version_no)
        REFERENCES survey_published_version (tenant_id, survey_id, version_no)
);

-- 最小授权：问卷与发布尝试不删除；已发布版本与映射只追加。
REVOKE DELETE ON survey, survey_publish_attempt FROM platform_app;
REVOKE UPDATE, DELETE ON survey_published_version, survey_question_binding FROM platform_app;

ALTER TABLE survey ENABLE ROW LEVEL SECURITY;
ALTER TABLE survey FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON survey
    USING (tenant_id = app_current_tenant()) WITH CHECK (tenant_id = app_current_tenant());

ALTER TABLE survey_publish_attempt ENABLE ROW LEVEL SECURITY;
ALTER TABLE survey_publish_attempt FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON survey_publish_attempt
    USING (tenant_id = app_current_tenant()) WITH CHECK (tenant_id = app_current_tenant());

ALTER TABLE survey_published_version ENABLE ROW LEVEL SECURITY;
ALTER TABLE survey_published_version FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON survey_published_version
    USING (tenant_id = app_current_tenant()) WITH CHECK (tenant_id = app_current_tenant());

ALTER TABLE survey_question_binding ENABLE ROW LEVEL SECURITY;
ALTER TABLE survey_question_binding FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON survey_question_binding
    USING (tenant_id = app_current_tenant()) WITH CHECK (tenant_id = app_current_tenant());
