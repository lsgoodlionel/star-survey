-- 每个租户一份 SMTP 出站配置。credential_ciphertext 仅保存 AES-GCM 密文；原始授权码
-- 只经受控写接口进入内存后立即加密，绝不由查询接口返回。
CREATE TABLE delivery_email_setting (
    tenant_id             uuid        PRIMARY KEY,
    provider              text        NOT NULL CHECK (provider IN ('gmail', 'outlook-com', 'netease-163')),
    auth_mode             text        NOT NULL CHECK (auth_mode IN ('app-password', 'oauth2')),
    from_address          text        NOT NULL CHECK (length(from_address) BETWEEN 3 AND 320),
    username              text        NOT NULL CHECK (length(username) BETWEEN 3 AND 320),
    credential_ciphertext bytea       NOT NULL CHECK (octet_length(credential_ciphertext) BETWEEN 17 AND 4096),
    credential_nonce      bytea       NOT NULL CHECK (octet_length(credential_nonce) = 12),
    updated_by            text        NOT NULL CHECK (length(updated_by) BETWEEN 1 AND 256),
    updated_at            timestamptz NOT NULL DEFAULT now()
);

ALTER TABLE delivery_email_setting ENABLE ROW LEVEL SECURITY;
ALTER TABLE delivery_email_setting FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON delivery_email_setting
    USING (tenant_id = app_current_tenant()) WITH CHECK (tenant_id = app_current_tenant());
