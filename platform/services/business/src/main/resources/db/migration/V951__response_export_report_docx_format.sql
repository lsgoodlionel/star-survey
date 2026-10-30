-- Wave 3 合并收口：V935 首次放开 report-docx，但并行开发的后置 V950 重新建立了
-- PDF 格式约束。使用新迁移统一最终集合，不修改任何已发布迁移的校验和。
ALTER TABLE response_export_job DROP CONSTRAINT response_export_job_format_check;
ALTER TABLE response_export_job
    ADD CONSTRAINT response_export_job_format_check
    CHECK (format IN ('csv', 'xlsx', 'sav', 'docx', 'attachments', 'pdf', 'report-docx'));
