-- 投放链接（WP-05 切片 05.1）。delivery 车道号段 V700–V799，不依赖其他车道的迁移。
--
-- 一个已发布问卷可以有多条链接：每条带一个来源标签（渠道归因）、一组签名业务参数、可选的短链与有效期。
-- 链接本身不保存作答地址——地址由引擎实例的 base_url 与已发布 sid 在运行期拼出（问卷重新发布后自动指向新版本）。
CREATE TABLE delivery_link (
    tenant_id     uuid        NOT NULL,
    id            uuid        NOT NULL,
    -- 问卷的公开 UUID。不建外键：本车道不引用 survey 车道的表（见 CONVENTIONS），
    -- 问卷是否存在、是否属于本租户由权限判定（access 资源树）决定。
    survey_id     uuid        NOT NULL,
    label         text        NOT NULL CHECK (length(label) BETWEEN 1 AND 64),
    -- 已签名的参数集合（含 exp 与 sig），原样拼到作答地址后面。
    signed_params jsonb       NOT NULL DEFAULT '{}'::jsonb,
    expires_at    timestamptz,
    created_by    text        NOT NULL CHECK (length(created_by) BETWEEN 1 AND 256),
    created_at    timestamptz NOT NULL DEFAULT now(),
    revoked_at    timestamptz,
    PRIMARY KEY (tenant_id, id)
);
CREATE INDEX delivery_link_survey ON delivery_link (tenant_id, survey_id, created_at DESC);

-- 短链登记：**控制平面表**，不做行级隔离，理由与租户登记、引擎实例登记相同（CONVENTIONS "其他"一节）——
-- 匿名的 /d/s/{code} 必须在还不知道租户的情况下解析出租户，才能进入该租户的作用域读链接内容。
-- 因此本表只存标识（短链码、租户、链接 id），不存标签、参数、问卷标题等任何租户业务数据；
-- 链接内容一律在 delivery_link 里，受行级安全保护。短链码有约 64 位熵，不可枚举。
CREATE TABLE delivery_short_link (
    code       text        PRIMARY KEY CHECK (code ~ '^[2-9a-hjkmnp-z]{13}$'),
    tenant_id  uuid        NOT NULL REFERENCES tenant (id),
    link_id    uuid        NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, link_id)
);
-- 短链一经登记不再改写（作废由 delivery_link.revoked_at 表达），运行期账号只能查询与新增。
REVOKE UPDATE, DELETE ON delivery_short_link FROM platform_app;

ALTER TABLE delivery_link ENABLE ROW LEVEL SECURITY;
ALTER TABLE delivery_link FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON delivery_link
    USING (tenant_id = app_current_tenant()) WITH CHECK (tenant_id = app_current_tenant());
