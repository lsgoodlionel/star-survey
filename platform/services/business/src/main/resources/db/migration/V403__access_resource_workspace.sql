-- 工作区列表的业务时间、归档状态与稳定排序索引。
-- 历史行必须保留原创建时间作为初始业务更新时间，不能以迁移时间重排。
ALTER TABLE access_resource ADD COLUMN updated_at timestamptz;
UPDATE access_resource SET updated_at = created_at;
ALTER TABLE access_resource ALTER COLUMN updated_at SET NOT NULL;
ALTER TABLE access_resource ALTER COLUMN updated_at SET DEFAULT now();
ALTER TABLE access_resource ADD COLUMN archived_at timestamptz;

CREATE INDEX access_resource_active_parent_list
    ON access_resource (tenant_id, parent_id, kind, updated_at DESC, name COLLATE "C", id)
    WHERE archived_at IS NULL;
CREATE INDEX access_resource_archived_parent_list
    ON access_resource (tenant_id, parent_id, kind, updated_at DESC, name COLLATE "C", id)
    WHERE archived_at IS NOT NULL;
