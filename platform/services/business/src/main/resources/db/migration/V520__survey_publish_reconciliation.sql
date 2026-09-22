-- 待核对发布的后台核对（survey 车道，V520–V529）。
-- 只给发布尝试加两列，不改任何行级安全策略、不开放任何跨租户读取：
-- 核对任务逐个租户进入 TenantScope 查自己的待办（见 PublishReconciler）。
--   next_reconcile_at：核对任务下一次可以重发的时间（退避）；为空表示随时可以。
--   manual_review_at ：结果未知的重发次数达到上限后，停止自动重试、标记人工复核的时间。
ALTER TABLE survey_publish_attempt
    ADD COLUMN next_reconcile_at timestamptz,
    ADD COLUMN manual_review_at  timestamptz;

-- 每轮每个租户扫描一次：只索引处于 publishing / pending_reconciliation 的少量问卷。
CREATE INDEX survey_reconcile_candidates ON survey (tenant_id, status)
    WHERE status IN ('publishing', 'pending_reconciliation');
