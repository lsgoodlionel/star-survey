-- 租户侧角色目录（WP-19 基础）。角色是数据：角色 -> 权限集合由本表决定，代码里不写 if/else 角色链。
-- 目录对所有租户相同，属于控制平面：不做行级隔离，运行期账号只读。

CREATE TABLE access_permission (
    code        text PRIMARY KEY,
    description text NOT NULL
);

CREATE TABLE access_role (
    code             text    PRIMARY KEY,
    name             text    NOT NULL,
    -- 只能授予整个租户（不能落在某个项目/文件夹/问卷上），如租户所有者、财务。
    tenant_wide_only boolean NOT NULL,
    -- 非空时授权必须带到期时间且不超过该时长（支持人员访问客户数据须限时授权）。
    max_grant_hours  integer CHECK (max_grant_hours > 0),
    -- 是否只能授予占用席位的成员；参与者不占席位。
    requires_seat    boolean NOT NULL
);

CREATE TABLE access_role_permission (
    role_code       text NOT NULL REFERENCES access_role (code),
    permission_code text NOT NULL REFERENCES access_permission (code),
    PRIMARY KEY (role_code, permission_code)
);

REVOKE INSERT, UPDATE, DELETE ON access_permission, access_role, access_role_permission FROM platform_app;

INSERT INTO access_permission (code, description) VALUES
    ('view',                     '查看项目、文件夹、问卷'),
    ('edit',                     '编辑'),
    ('publish',                  '发布（租户开启审核时还需免审权限或走审核）'),
    ('publish-without-approval', '发布无需审核（管理员）'),
    ('approve-publish',          '审核他人的发布申请'),
    ('view-statistics',          '查看统计结果'),
    ('view-raw-responses',       '查看答卷明细'),
    ('export-raw-responses',     '导出答卷明细'),
    ('view-sensitive-fields',    '查看敏感字段（手机号、证件号等）明文'),
    ('manage-members',           '管理成员与授权'),
    ('manage-settings',          '管理租户设置（如发布审核开关）'),
    ('view-billing',             '查看账单与订阅'),
    ('manage-billing',           '支付、开票、变更订阅'),
    ('grade',                    '阅卷评分'),
    ('interview',                '访员代填'),
    ('respond',                  '作为参与者作答');

INSERT INTO access_role (code, name, tenant_wide_only, max_grant_hours, requires_seat) VALUES
    ('tenant_owner',      '租户所有者',   true,  NULL, true),
    ('finance',           '财务',         true,  NULL, true),
    ('org_admin',         '组织管理员',   true,  NULL, true),
    ('project_manager',   '项目管理者',   false, NULL, true),
    ('editor',            '编辑者',       false, NULL, true),
    ('publish_reviewer',  '发布审核员',   false, NULL, true),
    ('raw_data_viewer',   '原始数据查看者', false, NULL, true),
    ('statistics_viewer', '统计查看者',   false, NULL, true),
    ('grader',            '阅卷员',       false, NULL, true),
    ('interviewer',       '访员',         false, NULL, true),
    ('participant',       '参与者',       false, NULL, false),
    ('support',           '支持人员',     false, 72,   true);

-- 租户所有者拥有全部权限。
INSERT INTO access_role_permission (role_code, permission_code)
SELECT 'tenant_owner', code FROM access_permission;

INSERT INTO access_role_permission (role_code, permission_code) VALUES
    ('finance', 'view-billing'),
    ('finance', 'manage-billing'),

    ('org_admin', 'view'),
    ('org_admin', 'edit'),
    ('org_admin', 'publish'),
    ('org_admin', 'publish-without-approval'),
    ('org_admin', 'approve-publish'),
    ('org_admin', 'view-statistics'),
    ('org_admin', 'view-raw-responses'),
    ('org_admin', 'export-raw-responses'),
    ('org_admin', 'manage-members'),
    ('org_admin', 'manage-settings'),

    ('project_manager', 'view'),
    ('project_manager', 'edit'),
    ('project_manager', 'publish'),
    ('project_manager', 'view-statistics'),
    ('project_manager', 'view-raw-responses'),
    ('project_manager', 'export-raw-responses'),
    ('project_manager', 'manage-members'),

    ('editor', 'view'),
    ('editor', 'edit'),
    ('editor', 'publish'),
    ('editor', 'view-statistics'),

    ('publish_reviewer', 'view'),
    ('publish_reviewer', 'approve-publish'),

    ('raw_data_viewer', 'view'),
    ('raw_data_viewer', 'view-statistics'),
    ('raw_data_viewer', 'view-raw-responses'),
    ('raw_data_viewer', 'export-raw-responses'),
    ('raw_data_viewer', 'view-sensitive-fields'),

    ('statistics_viewer', 'view'),
    ('statistics_viewer', 'view-statistics'),

    ('grader', 'view'),
    ('grader', 'view-raw-responses'),
    ('grader', 'grade'),

    ('interviewer', 'view'),
    ('interviewer', 'interview'),

    ('participant', 'respond'),

    ('support', 'view'),
    ('support', 'view-statistics');
