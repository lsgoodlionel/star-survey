-- 引擎事件收件箱：每个收到的事件记一行，按 eventId 去重。
-- 中继是"至少一次"，同一事件可能多次到达；主键冲突即判定为重复，不再产生任何效果。
--
-- 去重键带上租户与引擎实例：eventId 由引擎实例随机生成，重投一定来自同一实例；
-- 若只按 eventId 全局唯一，一个租户的事件就可能与另一个租户的事件在唯一索引上相互影响。
CREATE TABLE engine_event_inbox (
    tenant_id          uuid        NOT NULL,
    engine_instance_id text        NOT NULL CHECK (length(engine_instance_id) BETWEEN 1 AND 64),
    event_id           uuid        NOT NULL,
    event_type         text        NOT NULL CHECK (event_type IN ('response.saved', 'response.completed', 'response.deleted')),
    schema_version     integer     NOT NULL,
    survey_id          bigint      NOT NULL,
    generation         text        NOT NULL CHECK (length(generation) BETWEEN 1 AND 64),
    response_id        bigint      NOT NULL,
    source             text        NOT NULL CHECK (length(source) BETWEEN 1 AND 64),
    occurred_at        timestamptz NOT NULL,
    received_at        timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, engine_instance_id, event_id)
);
CREATE INDEX engine_event_inbox_response
    ON engine_event_inbox (tenant_id, engine_instance_id, survey_id, generation, response_id);

-- 只追加：收件箱是"平台收到过什么"的证据，运行期账号不能改、不能删。
REVOKE UPDATE, DELETE ON engine_event_inbox FROM platform_app;

ALTER TABLE engine_event_inbox ENABLE ROW LEVEL SECURITY;
ALTER TABLE engine_event_inbox FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON engine_event_inbox
    USING (tenant_id = app_current_tenant())
    WITH CHECK (tenant_id = app_current_tenant());
