-- SPSS 系统文件（.sav）导出格式（WP-06 切片 06.3，R06-03）。
--
-- 新格式只是 ADR 0015 决定 5 那个扩展点上的又一个 ExportFormat 实现：分片格式、作业模型、租约、
-- 下载再次授权都不变，只有最终文件的写出方不同。这里只放开 format 的取值，不动任何别的列。
ALTER TABLE response_export_job DROP CONSTRAINT response_export_job_format_check;
ALTER TABLE response_export_job
    ADD CONSTRAINT response_export_job_format_check CHECK (format IN ('csv', 'xlsx', 'sav'));
