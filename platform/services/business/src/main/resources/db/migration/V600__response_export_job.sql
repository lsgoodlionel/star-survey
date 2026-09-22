-- 答卷导出作业（WP-06 切片 06.2，ADR 0015）。response 车道号段 V600–V609，不依赖其他车道的迁移。
--
-- response_export_job：一行一个作业，不删除（审计线索）。导出计划（来源、代次、字段字典）创建时冻结在 plan 里；
-- 水位线就是 created_at：只有 created_at 不晚于它的投影行进入快照。
CREATE TABLE response_export_job (
    tenant_id        uuid        NOT NULL,
    id               uuid        NOT NULL,
    survey_id        uuid        NOT NULL,
    requested_by     text        NOT NULL CHECK (length(requested_by) BETWEEN 1 AND 256),
    format           text        NOT NULL CHECK (format IN ('csv', 'xlsx')),
    template_version text        NOT NULL DEFAULT 'default' CHECK (template_version = 'default'),
    filter_snapshot  jsonb       NOT NULL,
    plan             jsonb       NOT NULL,
    reveal_sensitive boolean     NOT NULL,
    -- 分片边界 = 1、1+batch_size、1+2×batch_size……创建时固定，配置变化不影响在途作业的恢复。
    batch_size       integer     NOT NULL CHECK (batch_size BETWEEN 1 AND 500),
    idempotency_key  text        CHECK (length(idempotency_key) BETWEEN 1 AND 128),
    status           text        NOT NULL DEFAULT 'queued'
                     CHECK (status IN ('queued', 'running', 'completed', 'failed', 'cancelled', 'expired')),
    -- 物化进度：正在扫的版本，以及该版本内已扫到的 (代次, 答卷号)；两者为空表示从该版本开头扫。
    snapshot_done    boolean     NOT NULL DEFAULT false,
    snapshot_version integer,
    snapshot_generation  text,
    snapshot_response_id bigint,
    total_rows       bigint      CHECK (total_rows >= 0),
    next_seq         bigint      NOT NULL DEFAULT 1 CHECK (next_seq >= 1),
    attempts         integer     NOT NULL DEFAULT 0 CHECK (attempts >= 0),
    next_attempt_at  timestamptz NOT NULL DEFAULT now(),
    lease_token      uuid,
    lease_until      timestamptz,
    last_error       text,
    file_key         text,
    file_size        bigint      CHECK (file_size >= 0),
    file_sha256      text        CHECK (file_sha256 ~ '^[0-9a-f]{64}$'),
    created_at       timestamptz NOT NULL DEFAULT now(),
    started_at       timestamptz,
    finished_at      timestamptz,
    expires_at       timestamptz NOT NULL,
    PRIMARY KEY (tenant_id, id),
    CHECK (expires_at > created_at),
    CHECK ((snapshot_generation IS NULL) = (snapshot_response_id IS NULL)),
    CHECK (status <> 'completed' OR (file_key IS NOT NULL AND file_size IS NOT NULL AND file_sha256 IS NOT NULL))
);
CREATE UNIQUE INDEX response_export_job_idempotency
    ON response_export_job (tenant_id, requested_by, idempotency_key) WHERE idempotency_key IS NOT NULL;
-- 后台认领：只扫未结束的作业。
CREATE INDEX response_export_job_open
    ON response_export_job (tenant_id, next_attempt_at) WHERE status IN ('queued', 'running');
CREATE INDEX response_export_job_expiry
    ON response_export_job (tenant_id, expires_at) WHERE status <> 'expired';

-- response_export_item：物化的快照，seq = 1..N 是导出顺序。作业完成或到期即删除。
CREATE TABLE response_export_item (
    tenant_id          uuid        NOT NULL,
    job_id             uuid        NOT NULL,
    seq                bigint      NOT NULL CHECK (seq >= 1),
    version_no         integer     NOT NULL,
    engine_instance_id text        NOT NULL,
    engine_sid         bigint      NOT NULL,
    generation         text        NOT NULL,
    response_id        bigint      NOT NULL,
    state              text        NOT NULL CHECK (state IN ('in_progress', 'engine_completed', 'deleted')),
    first_event_at     timestamptz NOT NULL,
    completed_at       timestamptz,
    PRIMARY KEY (tenant_id, job_id, seq),
    UNIQUE (tenant_id, job_id, version_no, generation, response_id),
    FOREIGN KEY (tenant_id, job_id) REFERENCES response_export_job (tenant_id, id)
);

REVOKE DELETE ON response_export_job FROM platform_app;
REVOKE UPDATE ON response_export_item FROM platform_app;

ALTER TABLE response_export_job ENABLE ROW LEVEL SECURITY;
ALTER TABLE response_export_job FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON response_export_job
    USING (tenant_id = app_current_tenant()) WITH CHECK (tenant_id = app_current_tenant());

ALTER TABLE response_export_item ENABLE ROW LEVEL SECURITY;
ALTER TABLE response_export_item FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON response_export_item
    USING (tenant_id = app_current_tenant()) WITH CHECK (tenant_id = app_current_tenant());
