-- 联系人导入作业（WP-18 切片 18.1，ADR 0017 决定 3）。
-- 作业可断点续跑：next_row 是"下一条待处理的行号"，它与该行的写入、该行的结果在同一个事务里推进，
-- 所以进程在任何时刻挂掉，恢复后都从同一行重来，既不漏也不会重复建人。
CREATE TABLE contact_import_job (
    tenant_id       uuid        NOT NULL,
    id              uuid        NOT NULL,
    list_id         uuid        NOT NULL,
    format          text        NOT NULL CHECK (format IN ('csv', 'json')),
    requested_by    text        NOT NULL CHECK (length(requested_by) BETWEEN 1 AND 256),
    idempotency_key text        CHECK (length(idempotency_key) BETWEEN 1 AND 128),
    status          text        NOT NULL DEFAULT 'queued'
                    CHECK (status IN ('queued', 'running', 'completed', 'failed', 'cancelled')),
    total_rows      integer     NOT NULL CHECK (total_rows >= 0),
    next_row        integer     NOT NULL DEFAULT 1 CHECK (next_row >= 1),
    created_count   integer     NOT NULL DEFAULT 0 CHECK (created_count >= 0),
    updated_count   integer     NOT NULL DEFAULT 0 CHECK (updated_count >= 0),
    duplicate_count integer     NOT NULL DEFAULT 0 CHECK (duplicate_count >= 0),
    invalid_count   integer     NOT NULL DEFAULT 0 CHECK (invalid_count >= 0),
    attempts        integer     NOT NULL DEFAULT 0 CHECK (attempts >= 0),
    next_attempt_at timestamptz NOT NULL DEFAULT now(),
    lease_token     uuid,
    lease_until     timestamptz,
    -- 只写错误码，永不写行内容（个人信息不进日志、不进错误字段）。
    last_error      text        CHECK (length(last_error) BETWEEN 1 AND 200),
    created_at      timestamptz NOT NULL DEFAULT now(),
    started_at      timestamptz,
    finished_at     timestamptz,
    PRIMARY KEY (tenant_id, id),
    FOREIGN KEY (tenant_id, list_id) REFERENCES contact_list (tenant_id, id),
    CHECK (next_row <= total_rows + 1)
);
CREATE UNIQUE INDEX contact_import_job_idempotency
    ON contact_import_job (tenant_id, requested_by, idempotency_key) WHERE idempotency_key IS NOT NULL;
CREATE INDEX contact_import_job_open
    ON contact_import_job (tenant_id, next_attempt_at) WHERE status IN ('queued', 'running');

-- 待处理的原始行（含个人信息）。作业结束即删除，只留不含个人信息的逐行结果。
CREATE TABLE contact_import_row (
    tenant_id uuid    NOT NULL,
    job_id    uuid    NOT NULL,
    row_no    integer NOT NULL CHECK (row_no >= 1),
    payload   jsonb   NOT NULL,
    PRIMARY KEY (tenant_id, job_id, row_no),
    FOREIGN KEY (tenant_id, job_id) REFERENCES contact_import_job (tenant_id, id)
);

-- 逐行结果：行号、结局、原因码、命中的联系人 id。**不含**任何行内容。
CREATE TABLE contact_import_outcome (
    tenant_id  uuid    NOT NULL,
    job_id     uuid    NOT NULL,
    row_no     integer NOT NULL CHECK (row_no >= 1),
    outcome    text    NOT NULL CHECK (outcome IN ('created', 'updated', 'duplicate', 'invalid')),
    reason     text    CHECK (reason ~ '^[a-z][a-z0-9_]{1,63}$'),
    contact_id uuid,
    PRIMARY KEY (tenant_id, job_id, row_no),
    FOREIGN KEY (tenant_id, job_id) REFERENCES contact_import_job (tenant_id, id),
    FOREIGN KEY (tenant_id, contact_id) REFERENCES contact (tenant_id, id),
    CHECK ((outcome IN ('created', 'updated')) = (contact_id IS NOT NULL))
);

REVOKE DELETE ON contact_import_job, contact_import_outcome FROM platform_app;
REVOKE UPDATE ON contact_import_outcome FROM platform_app;

ALTER TABLE contact_import_job ENABLE ROW LEVEL SECURITY;
ALTER TABLE contact_import_job FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON contact_import_job
    USING (tenant_id = app_current_tenant()) WITH CHECK (tenant_id = app_current_tenant());

ALTER TABLE contact_import_row ENABLE ROW LEVEL SECURITY;
ALTER TABLE contact_import_row FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON contact_import_row
    USING (tenant_id = app_current_tenant()) WITH CHECK (tenant_id = app_current_tenant());

ALTER TABLE contact_import_outcome ENABLE ROW LEVEL SECURITY;
ALTER TABLE contact_import_outcome FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON contact_import_outcome
    USING (tenant_id = app_current_tenant()) WITH CHECK (tenant_id = app_current_tenant());
