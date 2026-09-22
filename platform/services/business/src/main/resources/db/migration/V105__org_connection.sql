-- 组织授权连接（WP-20.1）：租户把自己在企业微信 / 钉钉 / 飞书的企业与应用接入平台，用于免登与组织同步。
--
-- corp_id：企业微信 corpid / 钉钉 corpId / 飞书 tenant_key，是外部身份的作用域（写进 identity_binding.app_id）；
-- app_id ：企业微信 agentid / 钉钉 AppKey(clientId) / 飞书 App ID(cli_…)。
-- secret_ref 只是密钥的名字，不是密钥本身：真正的值由运行环境按"租户 + 名字"解析（见 ADR 0014），
-- 数据库里永远没有明文密钥，别的租户也无法借用这个名字解析到本租户的密钥。
CREATE TABLE org_connection (
    id                  uuid        PRIMARY KEY,
    tenant_id           uuid        NOT NULL REFERENCES tenant (id),
    provider            text        NOT NULL CHECK (provider IN ('wecom', 'dingtalk', 'feishu')),
    corp_id             text        NOT NULL CHECK (corp_id ~ '^[A-Za-z0-9_.-]{1,128}$'),
    app_id              text        NOT NULL CHECK (app_id ~ '^[A-Za-z0-9_.-]{1,128}$'),
    secret_ref          text        NOT NULL CHECK (secret_ref ~ '^[A-Z][A-Z0-9_]{0,63}$'),
    unknown_user_policy text        NOT NULL CHECK (unknown_user_policy IN ('require_preauthorized', 'jit_staff')),
    enabled             boolean     NOT NULL,
    created_by          text        NOT NULL,
    created_at          timestamptz NOT NULL DEFAULT now(),
    updated_at          timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT org_connection_app_key UNIQUE (tenant_id, provider, corp_id, app_id)
);

-- 连接只停用不删除（登录会话与审计引用它）。
REVOKE DELETE ON org_connection FROM platform_app;

ALTER TABLE org_connection ENABLE ROW LEVEL SECURITY;
ALTER TABLE org_connection FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON org_connection
    USING (tenant_id = app_current_tenant()) WITH CHECK (tenant_id = app_current_tenant());
