-- 单份与批量答卷 PDF（R06-05）：与 DOCX 共用冻结作业及逐份答卷视图。
-- 中文字体随应用按 SIL OFL 内嵌，不依赖宿主机字体。
ALTER TABLE response_export_job DROP CONSTRAINT response_export_job_format_check;
ALTER TABLE response_export_job
    ADD CONSTRAINT response_export_job_format_check
    CHECK (format IN ('csv', 'xlsx', 'sav', 'docx', 'pdf', 'report-docx', 'attachments'));
