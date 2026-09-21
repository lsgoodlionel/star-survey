-- 额度模块（entitlement）：能力与计量的登记、套餐版本。
-- 套餐是配置不是代码（U-07）：产品能做的事登记为"能力"，每个能力是独立开关；
-- 套餐版本只决定开哪些能力、各计量给多少额度。

-- 计量定义（控制平面）。scope 决定额度按租户还是按问卷累计；reset_policy 决定何时归零。
CREATE TABLE meter_definition (
    code          text PRIMARY KEY CHECK (code ~ '^[a-z][a-z0-9_.]{1,63}$'),
    unit          text NOT NULL,
    scope         text NOT NULL CHECK (scope IN ('tenant', 'survey')),
    reset_policy  text NOT NULL CHECK (reset_policy IN ('subscription_period', 'never')),
    description   text NOT NULL
);

-- 能力定义（控制平面）。access 决定订阅只读时是否放行；meter_code 非空表示使用该能力要消耗额度。
CREATE TABLE capability_definition (
    code          text PRIMARY KEY CHECK (code ~ '^[a-z][a-z0-9_.]{1,63}$'),
    access        text NOT NULL CHECK (access IN ('read', 'export', 'write')),
    meter_code    text REFERENCES meter_definition (code),
    description   text NOT NULL
);

REVOKE INSERT, UPDATE, DELETE ON meter_definition, capability_definition FROM platform_app;

-- "有效完成答卷"：计费唯一口径（P-03）。预览、导入、删除、编辑都不计入。
-- 按问卷累计、永不归零，对应每份问卷 10 万 / 20 万 / 30 万的采集档位（P-02，档位值在套餐里配置）。
INSERT INTO meter_definition (code, unit, scope, reset_policy, description) VALUES
    ('response.valid_completed', 'response', 'survey', 'never', '有效完成答卷数（计费口径）'),
    ('ai.tokens', 'token', 'tenant', 'subscription_period', 'AI 能力消耗的模型 token 数（按能力 x 模型 x 计量入账）');

INSERT INTO capability_definition (code, access, meter_code, description) VALUES
    ('survey.read', 'read', NULL, '查看问卷与答卷'),
    ('survey.write', 'write', NULL, '创建、编辑、发布问卷'),
    ('response.collect', 'write', 'response.valid_completed', '对外收集答卷'),
    ('response.export', 'export', NULL, '导出答卷数据'),
    ('ai.survey_generate', 'write', 'ai.tokens', 'AI 生成问卷'),
    ('ai.response_analyze', 'write', 'ai.tokens', 'AI 分析答卷');

-- 套餐版本（控制平面）：一经发布不可修改，改动只能发布新版本（新行）。
-- quotas 形如 {"response.valid_completed": 100000}；缺失的计量视为 0（失败即关闭）。
CREATE TABLE plan_version (
    id                  uuid PRIMARY KEY,
    plan_code           text NOT NULL CHECK (plan_code ~ '^[a-z][a-z0-9_.-]{1,63}$'),
    version             integer NOT NULL CHECK (version >= 1),
    capabilities        text[] NOT NULL,
    quotas              jsonb NOT NULL CHECK (jsonb_typeof(quotas) = 'object'),
    export_window_days  integer NOT NULL CHECK (export_window_days >= 0),
    published_at        timestamptz NOT NULL DEFAULT now(),
    UNIQUE (plan_code, version)
);
REVOKE UPDATE, DELETE ON plan_version FROM platform_app;

-- 运行期账号已无改删权限；触发器再挡一层，让表所有者（迁移账号）同样改不了已发布版本。
CREATE FUNCTION reject_plan_version_change() RETURNS trigger
    LANGUAGE plpgsql
AS $$
BEGIN
    RAISE EXCEPTION 'plan_version is immutable: publish a new version instead';
END
$$;

CREATE TRIGGER plan_version_immutable
    BEFORE UPDATE OR DELETE ON plan_version
    FOR EACH ROW EXECUTE FUNCTION reject_plan_version_change();
