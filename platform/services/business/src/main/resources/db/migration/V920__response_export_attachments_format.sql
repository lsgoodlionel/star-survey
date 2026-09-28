-- 附件包导出格式（WP-06 切片 06.4 收尾，R06-07「附件打包」，ADR 0015 增补四）。
--
-- 与 V601（sav）、V602（docx）放开格式时同一个道理：附件包只是 ADR 0015 决定 5 那个扩展点上的
-- 又一个 ExportFormat 实现。水位线、快照、分批、租约、崩溃恢复、下载再次授权一律不变；
-- 新增的只是"分批时顺带把附件字节按份取回导出存储、收尾时流进 ZIP"，那一段不需要任何新的表或列——
-- 每一份附件的落地结果就是存储里有没有它（以及取不到时的那个墓碑对象）。
--
-- 这里只放开 format 的取值，不动任何别的列。
ALTER TABLE response_export_job DROP CONSTRAINT response_export_job_format_check;
ALTER TABLE response_export_job
    ADD CONSTRAINT response_export_job_format_check
    CHECK (format IN ('csv', 'xlsx', 'sav', 'docx', 'attachments'));
