-- 发布审批（R01-09，ADR 0011）。两张租户表，按行级安全模板隔离；只引用 V500 的 survey（已发布的旧迁移）。
--
-- 申请：绑定提交时的 draft_version。status 是审批状态机：
--   pending ──> approved ──> published
--      │           ├──> voided（草稿再次保存）
--      │           └──> withdrawn（申请人撤回）
--      ├──> rejected（须给原因）
--      ├──> withdrawn
--      └──> voided（草稿再次保存 / 问卷已按别的途径发布）
-- 状态迁移只在持有问卷行锁（survey FOR UPDATE）时进行：改稿、批准、发布都先锁问卷行，因此不可能批准或发布
-- 一个已被改动的草稿版本。
CREATE TABLE survey_publish_approval (
    tenant_id               uuid        NOT NULL,
    id                      uuid        NOT NULL,
    survey_id               uuid        NOT NULL,
    draft_version           integer     NOT NULL CHECK (draft_version > 0),
    status                  text        NOT NULL CHECK (status IN
                                ('pending', 'approved', 'rejected', 'withdrawn', 'voided', 'published')),
    applicant               text        NOT NULL,
    submitted_at            timestamptz NOT NULL DEFAULT now(),
    decided_by              text,
    decided_at              timestamptz,
    reason                  text        CHECK (reason IS NULL OR length(reason) <= 1000),
    -- 最近一次凭本批准发起的发布尝试（失败重试会换新的 requestId）。
    last_publish_request_id uuid,
    PRIMARY KEY (tenant_id, id),
    UNIQUE (id),
    FOREIGN KEY (tenant_id, survey_id) REFERENCES survey (tenant_id, id),
    CHECK ((status = 'pending') = (decided_at IS NULL)),
    CHECK (status <> 'rejected' OR reason IS NOT NULL)
);

-- 同一问卷同一时刻最多一条未结申请。
CREATE UNIQUE INDEX survey_publish_approval_one_open
    ON survey_publish_approval (tenant_id, survey_id) WHERE status IN ('pending', 'approved');
CREATE INDEX survey_publish_approval_pending
    ON survey_publish_approval (tenant_id, submitted_at) WHERE status = 'pending';

-- 决定历史：每次状态变化与每次凭批准发起的发布各一行，只追加。
CREATE TABLE survey_publish_approval_event (
    tenant_id          uuid        NOT NULL,
    id                 bigint      GENERATED ALWAYS AS IDENTITY,
    approval_id        uuid        NOT NULL,
    survey_id          uuid        NOT NULL,
    event              text        NOT NULL CHECK (event IN
                           ('submitted', 'approved', 'rejected', 'withdrawn', 'voided', 'publish_started', 'published')),
    actor              text        NOT NULL,
    draft_version      integer     NOT NULL,
    reason             text,
    publish_request_id uuid,
    occurred_at        timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, id),
    FOREIGN KEY (tenant_id, approval_id) REFERENCES survey_publish_approval (tenant_id, id)
);
CREATE INDEX survey_publish_approval_event_approval ON survey_publish_approval_event (tenant_id, approval_id, id);

-- 最小授权：申请不删除；决定历史只追加。
REVOKE DELETE ON survey_publish_approval FROM platform_app;
REVOKE UPDATE, DELETE ON survey_publish_approval_event FROM platform_app;

ALTER TABLE survey_publish_approval ENABLE ROW LEVEL SECURITY;
ALTER TABLE survey_publish_approval FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON survey_publish_approval
    USING (tenant_id = app_current_tenant()) WITH CHECK (tenant_id = app_current_tenant());

ALTER TABLE survey_publish_approval_event ENABLE ROW LEVEL SECURITY;
ALTER TABLE survey_publish_approval_event FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON survey_publish_approval_event
    USING (tenant_id = app_current_tenant()) WITH CHECK (tenant_id = app_current_tenant());
