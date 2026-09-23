-- 渠道回执与退订（WP-05 切片 05.2）。
--
-- 回执：渠道商投递结果的异步通知。(租户, 渠道商, 渠道商事件号) 唯一——同一条回执重发多少次都只落一行，
-- 因此既不会重复计费，也不会把一个收件人算成投递两次（验收"重复回执不重复计费"）。
CREATE TABLE delivery_receipt (
    tenant_id    uuid        NOT NULL,
    id           uuid        NOT NULL,
    provider     text        NOT NULL CHECK (length(provider) BETWEEN 1 AND 64),
    event_id     text        NOT NULL CHECK (length(event_id) BETWEEN 1 AND 128),
    kind         text        NOT NULL
                 CHECK (kind IN ('delivered', 'bounced', 'failed', 'complaint', 'unsubscribed')),
    task_id      uuid        NOT NULL,
    recipient_id uuid        NOT NULL,
    -- 是否计入计费：只有首次落库的回执会把任务的 billed_units 加一。
    billable     boolean     NOT NULL DEFAULT false,
    occurred_at  timestamptz,
    received_at  timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, id),
    UNIQUE (tenant_id, provider, event_id),
    FOREIGN KEY (tenant_id, task_id, recipient_id)
        REFERENCES delivery_recipient (tenant_id, task_id, id)
);
CREATE INDEX delivery_receipt_recipient ON delivery_receipt (tenant_id, task_id, recipient_id);
-- 回执是事实流水，只追加。
REVOKE UPDATE, DELETE ON delivery_receipt FROM platform_app;

-- 退订名单（租户级、按渠道）。一经登记不可撤销：运行期账号没有 UPDATE/DELETE，
-- 后续任何任务（包括催答）都必须先查这张表。重新订阅只能由收件人本人经另外的流程发起（尚未实现）。
CREATE TABLE delivery_unsubscribe (
    tenant_id      uuid        NOT NULL,
    channel        text        NOT NULL CHECK (channel IN ('email', 'sms')),
    address        text        NOT NULL CHECK (length(address) BETWEEN 3 AND 320),
    reason         text        NOT NULL CHECK (reason IN ('self', 'complaint', 'bounce', 'operator')),
    source_task_id uuid,
    created_at     timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, channel, address)
);
REVOKE UPDATE, DELETE ON delivery_unsubscribe FROM platform_app;

ALTER TABLE delivery_receipt ENABLE ROW LEVEL SECURITY;
ALTER TABLE delivery_receipt FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON delivery_receipt
    USING (tenant_id = app_current_tenant()) WITH CHECK (tenant_id = app_current_tenant());

ALTER TABLE delivery_unsubscribe ENABLE ROW LEVEL SECURITY;
ALTER TABLE delivery_unsubscribe FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON delivery_unsubscribe
    USING (tenant_id = app_current_tenant()) WITH CHECK (tenant_id = app_current_tenant());
