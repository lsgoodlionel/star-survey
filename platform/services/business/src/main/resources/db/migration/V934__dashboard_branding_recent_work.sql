-- 品牌编辑页沿用最近工作受控路由；仅扩展页面白名单，保留版本号约束。
ALTER TABLE dashboard_recent_work
    DROP CONSTRAINT dashboard_recent_work_page_check;

ALTER TABLE dashboard_recent_work
    ADD CONSTRAINT dashboard_recent_work_page_check
    CHECK (page IN ('edit', 'import', 'preview', 'publish', 'responses', 'version', 'branding'));
