-- 发件箱：与投影变更在同一事务写入，下游（额度计数等）据此消费，本车道不实现消费者。
-- 事务性发件箱保证：投影变了就一定有消息，投影没变就一定没有消息。
--
-- 每份答卷每种事件最多一行（唯一约束）：投影状态单调前进，同一跃迁不可能发生两次，
-- 这条约束是应用逻辑之外的第二道防线。
CREATE TABLE engine_outbox (
    id                 bigserial   PRIMARY KEY,
    tenant_id          uuid        NOT NULL,
    event_type         text        NOT NULL CHECK (event_type IN ('response.engine_completed', 'response.deleted')),
    engine_instance_id text        NOT NULL,
    survey_id          bigint      NOT NULL,
    generation         text        NOT NULL,
    response_id        bigint      NOT NULL,
    previous_state     text        CHECK (previous_state IN ('in_progress', 'engine_completed', 'deleted')),
    source_event_id    uuid        NOT NULL,
    occurred_at        timestamptz NOT NULL,
    payload            jsonb       NOT NULL,
    created_at         timestamptz NOT NULL DEFAULT now(),
    published_at       timestamptz,
    UNIQUE (tenant_id, engine_instance_id, survey_id, generation, response_id, event_type)
);
-- 发布者按租户扫描未发布的消息。
CREATE INDEX engine_outbox_unpublished ON engine_outbox (tenant_id, id) WHERE published_at IS NULL;

-- 发布者只需标记 published_at；清理过期消息由维护任务以所有者身份执行，运行期账号不能删。
REVOKE DELETE ON engine_outbox FROM platform_app;

ALTER TABLE engine_outbox ENABLE ROW LEVEL SECURITY;
ALTER TABLE engine_outbox FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON engine_outbox
    USING (tenant_id = app_current_tenant())
    WITH CHECK (tenant_id = app_current_tenant());
