-- 答卷投影：平台视角下每份答卷的最新状态。
-- 主键是答卷自然键 (租户, 引擎实例, 问卷, 代次, 答卷号)：停用再激活会让答卷号从 1 重新计数，
-- 代次不同即是不同的答卷（ADR 0003）。
-- 状态只前进不后退：in_progress(1) < engine_completed(2) < deleted(3)，由应用在行锁下判定。
CREATE TABLE response_projection (
    tenant_id          uuid        NOT NULL,
    engine_instance_id text        NOT NULL CHECK (length(engine_instance_id) BETWEEN 1 AND 64),
    survey_id          bigint      NOT NULL,
    generation         text        NOT NULL CHECK (length(generation) BETWEEN 1 AND 64),
    response_id        bigint      NOT NULL,
    state              text        NOT NULL CHECK (state IN ('in_progress', 'engine_completed', 'deleted')),
    first_event_at     timestamptz NOT NULL,
    completed_at       timestamptz,
    deleted_at         timestamptz,
    last_event_id      uuid        NOT NULL,
    created_at         timestamptz NOT NULL DEFAULT now(),
    updated_at         timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, engine_instance_id, survey_id, generation, response_id),
    CHECK (state <> 'deleted' OR deleted_at IS NOT NULL)
);
CREATE INDEX response_projection_survey_state
    ON response_projection (tenant_id, survey_id, state);

-- 删除是一种状态（墓碑），不是删行：删了行，迟到的完成事件就会把答卷"复活"。
REVOKE DELETE ON response_projection FROM platform_app;

ALTER TABLE response_projection ENABLE ROW LEVEL SECURITY;
ALTER TABLE response_projection FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON response_projection
    USING (tenant_id = app_current_tenant())
    WITH CHECK (tenant_id = app_current_tenant());
