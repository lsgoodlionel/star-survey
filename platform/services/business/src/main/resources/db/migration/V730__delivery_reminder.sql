-- 催答与新答卷通知（WP-05 切片 05.3）。

-- 提醒规则：挂在一次首邀任务上。首邀发出 first_delay_seconds 之后第一次催，
-- 此后每 interval_seconds 一次，同一个人最多 max_reminders 次。
CREATE TABLE delivery_reminder_rule (
    tenant_id           uuid        NOT NULL,
    id                  uuid        NOT NULL,
    survey_id           uuid        NOT NULL,
    source_task_id      uuid        NOT NULL,
    first_delay_seconds integer     NOT NULL CHECK (first_delay_seconds BETWEEN 0 AND 2592000),
    interval_seconds    integer     NOT NULL CHECK (interval_seconds BETWEEN 1 AND 2592000),
    max_reminders       integer     NOT NULL CHECK (max_reminders BETWEEN 1 AND 10),
    subject             text        CHECK (subject IS NULL OR length(subject) BETWEEN 1 AND 200),
    body_template       text        NOT NULL CHECK (length(body_template) BETWEEN 1 AND 4000),
    enabled             boolean     NOT NULL DEFAULT true,
    created_by          text        NOT NULL CHECK (length(created_by) BETWEEN 1 AND 256),
    created_at          timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, id),
    -- 一次首邀任务最多一条规则：否则同一个人会被两条规则各催一遍。
    UNIQUE (tenant_id, source_task_id),
    FOREIGN KEY (tenant_id, source_task_id) REFERENCES delivery_task (tenant_id, id)
);

-- 应答者完成记录：平台视角下"这个人把这份问卷答完了"。
-- 只追加、不删除——删掉一行就等于让已完成的人重新收到催答。
-- 来源 source：projection = 由引擎答卷投影对账得出；api = 集成方显式登记。
CREATE TABLE delivery_completion (
    tenant_id      uuid        NOT NULL,
    survey_id      uuid        NOT NULL,
    respondent_key text        NOT NULL CHECK (length(respondent_key) BETWEEN 1 AND 64),
    completed_at   timestamptz NOT NULL,
    source         text        NOT NULL CHECK (source IN ('projection', 'api')),
    recorded_at    timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, survey_id, respondent_key)
);
REVOKE DELETE ON delivery_completion FROM platform_app;

-- 完成对账的游标：上次扫到引擎答卷投影的哪一行（键集分页的位置）。
-- 有了它，每轮只看新增的已完成答卷，不必把整份问卷重扫一遍。
CREATE TABLE delivery_completion_cursor (
    tenant_id   uuid        NOT NULL,
    survey_id   uuid        NOT NULL,
    generation  text        NOT NULL,
    response_id bigint      NOT NULL,
    updated_at  timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, survey_id)
);

-- 新答卷通知规则：通知本租户已配置的成员，按最小间隔合并成一条"新增 N 份答卷"，不逐份轰炸。
-- notified_completed_count 是上次通知时该问卷的已完成答卷数；下次只在数量增加时通知。
CREATE TABLE delivery_notification_rule (
    tenant_id                uuid        NOT NULL,
    id                       uuid        NOT NULL,
    survey_id                uuid        NOT NULL,
    -- 收件成员的 actor 标识：发送前按 access 判定其对该问卷是否仍有查看权，无权即跳过。
    actor_id                 text        NOT NULL CHECK (length(actor_id) BETWEEN 1 AND 256),
    channel                  text        NOT NULL CHECK (channel IN ('email', 'sms')),
    address                  text        NOT NULL CHECK (length(address) BETWEEN 3 AND 320),
    min_interval_seconds     integer     NOT NULL CHECK (min_interval_seconds BETWEEN 60 AND 86400),
    notified_completed_count bigint      NOT NULL DEFAULT 0 CHECK (notified_completed_count >= 0),
    last_notified_at         timestamptz,
    enabled                  boolean     NOT NULL DEFAULT true,
    created_by               text        NOT NULL CHECK (length(created_by) BETWEEN 1 AND 256),
    created_at               timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, id),
    UNIQUE (tenant_id, survey_id, actor_id, channel)
);

ALTER TABLE delivery_reminder_rule ENABLE ROW LEVEL SECURITY;
ALTER TABLE delivery_reminder_rule FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON delivery_reminder_rule
    USING (tenant_id = app_current_tenant()) WITH CHECK (tenant_id = app_current_tenant());

ALTER TABLE delivery_completion ENABLE ROW LEVEL SECURITY;
ALTER TABLE delivery_completion FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON delivery_completion
    USING (tenant_id = app_current_tenant()) WITH CHECK (tenant_id = app_current_tenant());

ALTER TABLE delivery_completion_cursor ENABLE ROW LEVEL SECURITY;
ALTER TABLE delivery_completion_cursor FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON delivery_completion_cursor
    USING (tenant_id = app_current_tenant()) WITH CHECK (tenant_id = app_current_tenant());

ALTER TABLE delivery_notification_rule ENABLE ROW LEVEL SECURITY;
ALTER TABLE delivery_notification_rule FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON delivery_notification_rule
    USING (tenant_id = app_current_tenant()) WITH CHECK (tenant_id = app_current_tenant());
