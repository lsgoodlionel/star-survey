-- 用户级最近工作。只保存受控页面枚举和可选版本号，恢复路径始终由服务端生成。
CREATE TABLE dashboard_recent_work (
    tenant_id  uuid        NOT NULL,
    actor_id   text        NOT NULL,
    survey_id  uuid        NOT NULL,
    page       text        NOT NULL CHECK (page IN ('edit', 'import', 'preview', 'publish', 'responses', 'version')),
    version_no integer,
    visited_at timestamptz NOT NULL,
    FOREIGN KEY (tenant_id, actor_id) REFERENCES access_member (tenant_id, actor_id),
    FOREIGN KEY (tenant_id, survey_id) REFERENCES survey (tenant_id, id),
    CHECK ((page = 'version' AND version_no IS NOT NULL AND version_no > 0)
        OR (page <> 'version' AND version_no IS NULL)),
    UNIQUE NULLS NOT DISTINCT (tenant_id, actor_id, survey_id, page, version_no)
);

CREATE INDEX dashboard_recent_work_latest
    ON dashboard_recent_work (tenant_id, actor_id, visited_at DESC, survey_id, page, version_no);

ALTER TABLE dashboard_recent_work ENABLE ROW LEVEL SECURITY;
ALTER TABLE dashboard_recent_work FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON dashboard_recent_work
    USING (tenant_id = app_current_tenant()) WITH CHECK (tenant_id = app_current_tenant());
