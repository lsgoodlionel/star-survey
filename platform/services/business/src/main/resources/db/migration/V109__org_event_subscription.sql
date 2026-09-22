-- 通讯录事件回调（企业微信 / 钉钉 / 飞书，ADR 0014 增补二）。
--
-- 1. 连接上登记事件订阅用的两个密钥名（不是密钥本身）：
--    event_token_ref：企业微信回调 Token / 钉钉事件订阅 token / 飞书 Verification Token；
--    event_key_ref  ：企业微信 EncodingAESKey / 钉钉 aes_key / 飞书 Encrypt Key。
--    与 secret_ref 一样按"租户 + 名字"从运行环境解析；两者要么都配、要么都不配（不配即不接收事件）。
ALTER TABLE org_connection
    ADD COLUMN event_token_ref text CHECK (event_token_ref ~ '^[A-Z][A-Z0-9_]{0,63}$'),
    ADD COLUMN event_key_ref   text CHECK (event_key_ref ~ '^[A-Z][A-Z0-9_]{0,63}$'),
    ADD CONSTRAINT org_connection_event_refs_together
        CHECK ((event_token_ref IS NULL) = (event_key_ref IS NULL));

-- 2. 已处理事件的回执，用于去重：开放平台会重试（飞书按 event_id、企业微信 / 钉钉按解密后的消息体摘要）。
--    event_key 是提供方事件标识（或消息体）的 SHA-256 十六进制，不存原文。
--    只在处理成功后写入：处理失败时没有回执，开放平台重试会被重新处理（撤权本身是幂等的）。
CREATE TABLE org_event_receipt (
    tenant_id     uuid        NOT NULL REFERENCES tenant (id),
    connection_id uuid        NOT NULL,
    event_key     text        NOT NULL CHECK (event_key ~ '^[0-9a-f]{64}$'),
    event_type    text        NOT NULL CHECK (event_type ~ '^[A-Za-z0-9_.:-]{1,128}$'),
    departed      integer     NOT NULL CHECK (departed >= 0),
    received_at   timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT org_event_receipt_pkey PRIMARY KEY (tenant_id, connection_id, event_key),
    -- 带 tenant_id 的复合外键：外键检查绕过行级安全，只按 id 引用会让 B 的行指向 A 的连接（见 V108）。
    CONSTRAINT org_event_receipt_connection_fkey
        FOREIGN KEY (tenant_id, connection_id) REFERENCES org_connection (tenant_id, id)
);
CREATE INDEX org_event_receipt_received ON org_event_receipt (tenant_id, received_at);

-- 回执只追加。
REVOKE UPDATE, DELETE ON org_event_receipt FROM platform_app;

ALTER TABLE org_event_receipt ENABLE ROW LEVEL SECURITY;
ALTER TABLE org_event_receipt FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON org_event_receipt
    USING (tenant_id = app_current_tenant()) WITH CHECK (tenant_id = app_current_tenant());
