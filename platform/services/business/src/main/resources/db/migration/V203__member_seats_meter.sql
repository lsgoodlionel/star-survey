-- 成员席位：套餐里配置上限（quotas 中的 member.seats），供权限模块在邀请成员时查询。
-- 席位占用由权限模块自己按成员状态统计，不走用量账本，因此这里只登记计量、不登记能力。
INSERT INTO meter_definition (code, unit, scope, reset_policy, description) VALUES
    ('member.seats', 'seat', 'tenant', 'never', '成员席位上限（按租户，不随订阅期归零）');
