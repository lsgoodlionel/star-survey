-- 用量账本：预占（reserve）-> 结算（capture）| 释放（release）| 过期回收（expire）。
--
-- usage_ledger_entry 是事实来源，只追加：每个预占键（reservation_key）至多一条 reserve、
-- 至多一条结算类流水（capture/release/expire 三选一），由两条部分唯一索引在数据库层保证，
-- 因此重试不会重复预占或重复结算，"释放后结算""结算后释放""回收两次"都会撞唯一索引。
--
-- usage_counter 是按（计量, 对象, 周期）汇总的余额投影，只做条件更新：
--   UPDATE ... SET reserved = reserved + q WHERE reserved + captured + q <= limit
-- PostgreSQL 在行锁上串行化并发更新，并在拿到锁后按最新行重新判断 WHERE，
-- 所以并发预占永远不会超过额度。

CREATE TABLE usage_counter (
    tenant_id   uuid   NOT NULL,
    meter_code  text   NOT NULL REFERENCES meter_definition (code),
    subject     text   NOT NULL,
    period_key  text   NOT NULL,
    reserved    bigint NOT NULL DEFAULT 0 CHECK (reserved >= 0),
    captured    bigint NOT NULL DEFAULT 0 CHECK (captured >= 0),
    updated_at  timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, meter_code, subject, period_key)
);
REVOKE DELETE ON usage_counter FROM platform_app;

ALTER TABLE usage_counter ENABLE ROW LEVEL SECURITY;
ALTER TABLE usage_counter FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON usage_counter
    USING (tenant_id = app_current_tenant())
    WITH CHECK (tenant_id = app_current_tenant());

CREATE TABLE usage_ledger_entry (
    id               bigserial PRIMARY KEY,
    tenant_id        uuid   NOT NULL,
    reservation_key  text   NOT NULL CHECK (length(reservation_key) BETWEEN 1 AND 200),
    kind             text   NOT NULL CHECK (kind IN ('reserve', 'capture', 'release', 'expire')),
    subscription_id  uuid   NOT NULL REFERENCES subscription (id),
    meter_code       text   NOT NULL REFERENCES meter_definition (code),
    subject          text   NOT NULL,
    period_key       text   NOT NULL,
    capability_code  text   REFERENCES capability_definition (code),
    model            text,
    quantity         bigint NOT NULL CHECK (quantity >= 0),
    expires_at       timestamptz,
    actor_id         text   NOT NULL,
    trace_id         text,
    occurred_at      timestamptz NOT NULL DEFAULT now(),
    CHECK ((kind = 'reserve') = (expires_at IS NOT NULL))
);
CREATE UNIQUE INDEX usage_ledger_one_reserve
    ON usage_ledger_entry (tenant_id, reservation_key) WHERE kind = 'reserve';
CREATE UNIQUE INDEX usage_ledger_one_settlement
    ON usage_ledger_entry (tenant_id, reservation_key) WHERE kind <> 'reserve';
CREATE INDEX usage_ledger_reserve_expiry
    ON usage_ledger_entry (tenant_id, expires_at) WHERE kind = 'reserve';
REVOKE UPDATE, DELETE ON usage_ledger_entry FROM platform_app;

ALTER TABLE usage_ledger_entry ENABLE ROW LEVEL SECURITY;
ALTER TABLE usage_ledger_entry FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON usage_ledger_entry
    USING (tenant_id = app_current_tenant())
    WITH CHECK (tenant_id = app_current_tenant());
