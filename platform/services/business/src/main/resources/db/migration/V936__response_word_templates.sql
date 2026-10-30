-- R06-06：问卷范围、不可变版本的自定义 Word 模板。
CREATE TABLE response_word_template (
    tenant_id      uuid        NOT NULL,
    survey_id      uuid        NOT NULL,
    id             uuid        NOT NULL,
    name           text        NOT NULL CHECK (length(name) BETWEEN 1 AND 200),
    current_version integer    NOT NULL DEFAULT 1 CHECK (current_version >= 1),
    created_by     text        NOT NULL,
    created_at     timestamptz NOT NULL DEFAULT now(),
    updated_at     timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, id)
);

CREATE TABLE response_word_template_version (
    tenant_id   uuid        NOT NULL,
    template_id uuid        NOT NULL,
    version_no  integer     NOT NULL CHECK (version_no >= 1),
    content     bytea       NOT NULL CHECK (octet_length(content) > 0),
    byte_size   bigint      NOT NULL CHECK (byte_size > 0),
    sha256      text        NOT NULL CHECK (sha256 ~ '^[0-9a-f]{64}$'),
    created_by  text        NOT NULL,
    created_at  timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, template_id, version_no),
    FOREIGN KEY (tenant_id, template_id) REFERENCES response_word_template (tenant_id, id)
);

CREATE INDEX response_word_template_survey
    ON response_word_template (tenant_id, survey_id, updated_at DESC, id);

ALTER TABLE response_word_template ENABLE ROW LEVEL SECURITY;
ALTER TABLE response_word_template FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON response_word_template
    USING (tenant_id = app_current_tenant()) WITH CHECK (tenant_id = app_current_tenant());

ALTER TABLE response_word_template_version ENABLE ROW LEVEL SECURITY;
ALTER TABLE response_word_template_version FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON response_word_template_version
    USING (tenant_id = app_current_tenant()) WITH CHECK (tenant_id = app_current_tenant());

-- 创建导出任务时把模板字节与摘要冻结到作业；模板以后增加新版本不会改变在途或历史任务。
ALTER TABLE response_export_job DROP CONSTRAINT response_export_job_template_version_check;
ALTER TABLE response_export_job
    ADD COLUMN template_content bytea,
    ADD COLUMN template_sha256 text CHECK (template_sha256 ~ '^[0-9a-f]{64}$'),
    ADD CONSTRAINT response_export_job_template_version_check CHECK (
        template_version = 'default'
        OR template_version ~ '^custom:[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}:[1-9][0-9]*$'
    ),
    ADD CONSTRAINT response_export_job_template_snapshot_check CHECK (
        (template_version = 'default' AND template_content IS NULL AND template_sha256 IS NULL)
        OR (template_version <> 'default' AND format = 'docx' AND template_content IS NOT NULL AND template_sha256 IS NOT NULL)
    );

REVOKE DELETE ON response_word_template FROM platform_app;
REVOKE UPDATE ON response_word_template_version FROM platform_app;
REVOKE DELETE ON response_word_template_version FROM platform_app;
