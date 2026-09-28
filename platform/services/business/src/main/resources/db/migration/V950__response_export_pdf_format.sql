-- 逐份答卷成文的 PDF 导出格式（WP-06 切片 06.4 收尾，R06-05，ADR 0015 增补六）。
--
-- 与 V601（sav）、V602（docx）、V920（attachments）放开格式时同一个道理：新格式只是 ADR 0015
-- 决定 5 那个扩展点上的又一个 ExportFormat 实现。作业模型、水位线、快照、分片、租约、崩溃恢复、
-- 下载再次授权一律不变，只有最终文件的写出方不同。
--
-- PDF 与前几种的区别在字体而不在作业：它必须**自带并嵌入**中文字体（冻结工作表 P-10，
-- 靠阅读器 CJK 字体包那条备选已被否决）。字体是随仓库交付的资源文件
-- （src/main/resources/fonts/MJYSansSC-Regular.ttf），不需要任何表或列。
--
-- 这里只放开 format 的取值，不动任何别的列。
ALTER TABLE response_export_job DROP CONSTRAINT response_export_job_format_check;
ALTER TABLE response_export_job
    ADD CONSTRAINT response_export_job_format_check
    CHECK (format IN ('csv', 'xlsx', 'sav', 'docx', 'attachments', 'pdf'));
