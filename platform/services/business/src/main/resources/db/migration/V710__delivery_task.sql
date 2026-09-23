-- 批量投放任务与收件人（WP-05 切片 05.2）。
--
-- 一个任务 = 一条渠道（短信/邮件）上的一批收件人。任务可恢复：进程中途死掉后，
-- 收件人各自的状态与发送台账（delivery_send）保证不会重复发送（台账里的幂等键原样交给渠道商）。

CREATE TABLE delivery_task (
    tenant_id       uuid        NOT NULL,
    id              uuid        NOT NULL,
    survey_id       uuid        NOT NULL,
    link_id         uuid,
    channel         text        NOT NULL CHECK (channel IN ('email', 'sms')),
    -- invite：首次邀请；reminder：催答（由提醒规则派生）；notification：新答卷通知
    kind            text        NOT NULL CHECK (kind IN ('invite', 'reminder', 'notification')),
    subject         text        CHECK (subject IS NULL OR length(subject) BETWEEN 1 AND 200),
    body_template   text        NOT NULL CHECK (length(body_template) BETWEEN 1 AND 4000),
    status          text        NOT NULL DEFAULT 'queued'
                    CHECK (status IN ('queued', 'running', 'completed', 'cancelled', 'failed')),
    source_task_id  uuid,
    idempotency_key text        CHECK (length(idempotency_key) BETWEEN 1 AND 128),
    queued_count    integer     NOT NULL DEFAULT 0 CHECK (queued_count >= 0),
    sent_count      integer     NOT NULL DEFAULT 0 CHECK (sent_count >= 0),
    failed_count    integer     NOT NULL DEFAULT 0 CHECK (failed_count >= 0),
    skipped_count   integer     NOT NULL DEFAULT 0 CHECK (skipped_count >= 0),
    -- 计费单元：只在回执首次落库时加一，重复回执不再加（验收"重复回执不重复计费"）。
    billed_units    integer     NOT NULL DEFAULT 0 CHECK (billed_units >= 0),
    attempts        integer     NOT NULL DEFAULT 0 CHECK (attempts >= 0),
    next_attempt_at timestamptz NOT NULL DEFAULT now(),
    last_error      text,
    created_by      text        NOT NULL CHECK (length(created_by) BETWEEN 1 AND 256),
    created_at      timestamptz NOT NULL DEFAULT now(),
    started_at      timestamptz,
    finished_at     timestamptz,
    PRIMARY KEY (tenant_id, id),
    FOREIGN KEY (tenant_id, link_id) REFERENCES delivery_link (tenant_id, id)
);
CREATE UNIQUE INDEX delivery_task_idempotency
    ON delivery_task (tenant_id, created_by, idempotency_key) WHERE idempotency_key IS NOT NULL;
CREATE INDEX delivery_task_open
    ON delivery_task (tenant_id, next_attempt_at) WHERE status IN ('queued', 'running');
CREATE INDEX delivery_task_by_source ON delivery_task (tenant_id, source_task_id) WHERE source_task_id IS NOT NULL;

-- 收件人。address 是租户联系数据（手机号/邮箱）：只在拼消息时读出，绝不进日志、绝不进错误信息。
-- respondent_key 是"这个人"在问卷里的标识（有邀请码用邀请码，否则用一个不可预测的随机键），
-- 催答据此判断是否已完成作答；它会作为签名参数 rk 出现在链接里，因此不可伪造。
CREATE TABLE delivery_recipient (
    tenant_id           uuid        NOT NULL,
    task_id             uuid        NOT NULL,
    id                  uuid        NOT NULL,
    address             text        NOT NULL CHECK (length(address) BETWEEN 3 AND 320),
    display_name        text        CHECK (display_name IS NULL OR length(display_name) BETWEEN 1 AND 128),
    engine_token        text        CHECK (engine_token IS NULL OR length(engine_token) BETWEEN 1 AND 64),
    respondent_key      text        NOT NULL CHECK (length(respondent_key) BETWEEN 1 AND 64),
    params              jsonb       NOT NULL DEFAULT '{}'::jsonb,
    state               text        NOT NULL DEFAULT 'queued'
                        CHECK (state IN ('queued', 'sending', 'sent', 'failed', 'bounced',
                                         'unsubscribed', 'skipped')),
    attempts            integer     NOT NULL DEFAULT 0 CHECK (attempts >= 0),
    provider_message_id text,
    last_error          text,
    -- 催答计数与上次催答时刻都记在"首次邀请"任务的收件人行上：加锁的就是这一行。
    reminders_sent      integer     NOT NULL DEFAULT 0 CHECK (reminders_sent >= 0),
    last_reminder_at    timestamptz,
    sent_at             timestamptz,
    updated_at          timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, task_id, id),
    -- 同一任务里一个地址只出现一次：重复提交的名单不会被投递两次。
    UNIQUE (tenant_id, task_id, address),
    FOREIGN KEY (tenant_id, task_id) REFERENCES delivery_task (tenant_id, id)
);
CREATE INDEX delivery_recipient_pending
    ON delivery_recipient (tenant_id, task_id) WHERE state IN ('queued', 'sending');
CREATE INDEX delivery_recipient_reminder
    ON delivery_recipient (tenant_id, task_id, last_reminder_at) WHERE state = 'sent';

-- 发送台账：一个 (任务, 收件人) 只可能有一行，幂等键随行固定。
-- 认领（写本行）与实际发送分处两个事务：进程在中间死掉时，重跑用同一个幂等键再发，
-- 渠道商据此只投递一次；台账不可删除，所以"发过一次"这个事实不会丢。
CREATE TABLE delivery_send (
    tenant_id           uuid        NOT NULL,
    task_id             uuid        NOT NULL,
    recipient_id        uuid        NOT NULL,
    idempotency_key     text        NOT NULL CHECK (length(idempotency_key) BETWEEN 1 AND 128),
    provider            text,
    provider_message_id text,
    claimed_at          timestamptz NOT NULL DEFAULT now(),
    completed_at        timestamptz,
    PRIMARY KEY (tenant_id, task_id, recipient_id),
    UNIQUE (tenant_id, idempotency_key),
    FOREIGN KEY (tenant_id, task_id, recipient_id)
        REFERENCES delivery_recipient (tenant_id, task_id, id)
);
-- 部分渠道商的回执只回自己的消息号，不回平台的幂等键；台账只追加、永远变大，这条反查必须走索引。
CREATE INDEX delivery_send_provider_message
    ON delivery_send (tenant_id, provider_message_id) WHERE provider_message_id IS NOT NULL;
REVOKE DELETE ON delivery_send FROM platform_app;

-- 每租户每渠道的固定窗口限速：一轮里发多少条由 used 决定，窗口过去即作废。
CREATE TABLE delivery_rate_window (
    tenant_id    uuid        NOT NULL,
    channel      text        NOT NULL CHECK (channel IN ('email', 'sms')),
    window_start timestamptz NOT NULL,
    used         integer     NOT NULL DEFAULT 0 CHECK (used >= 0),
    PRIMARY KEY (tenant_id, channel, window_start)
);

ALTER TABLE delivery_task ENABLE ROW LEVEL SECURITY;
ALTER TABLE delivery_task FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON delivery_task
    USING (tenant_id = app_current_tenant()) WITH CHECK (tenant_id = app_current_tenant());

ALTER TABLE delivery_recipient ENABLE ROW LEVEL SECURITY;
ALTER TABLE delivery_recipient FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON delivery_recipient
    USING (tenant_id = app_current_tenant()) WITH CHECK (tenant_id = app_current_tenant());

ALTER TABLE delivery_send ENABLE ROW LEVEL SECURITY;
ALTER TABLE delivery_send FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON delivery_send
    USING (tenant_id = app_current_tenant()) WITH CHECK (tenant_id = app_current_tenant());

ALTER TABLE delivery_rate_window ENABLE ROW LEVEL SECURITY;
ALTER TABLE delivery_rate_window FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON delivery_rate_window
    USING (tenant_id = app_current_tenant()) WITH CHECK (tenant_id = app_current_tenant());
