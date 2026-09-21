-- 订阅期：每个租户按时间追加的订阅记录，绑定一个套餐版本。
-- 换套餐、续费、取消都追加新行，历史期从不改写；运行期账号没有 UPDATE/DELETE。
-- 当前订阅 = 已开始的最新一期；到期状态由 ends_at 与当前时间推导，不需要定时任务改行。
CREATE TABLE subscription (
    id               uuid PRIMARY KEY,
    seq              bigserial NOT NULL,
    tenant_id        uuid NOT NULL,
    plan_version_id  uuid NOT NULL REFERENCES plan_version (id),
    kind             text NOT NULL CHECK (kind IN ('trial', 'paid', 'cancelled')),
    starts_at        timestamptz NOT NULL,
    ends_at          timestamptz NOT NULL,
    created_at       timestamptz NOT NULL DEFAULT now(),
    CHECK (ends_at >= starts_at)
);
CREATE INDEX subscription_tenant_start ON subscription (tenant_id, starts_at DESC, seq DESC);
REVOKE UPDATE, DELETE ON subscription FROM platform_app;

ALTER TABLE subscription ENABLE ROW LEVEL SECURITY;
ALTER TABLE subscription FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON subscription
    USING (tenant_id = app_current_tenant())
    WITH CHECK (tenant_id = app_current_tenant());
