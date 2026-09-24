-- 问卷受众：一份问卷要邀请哪一批联系人（WP-18 / WP-05 接缝，ADR 0016 邀请码）。
--
-- 存的是**条件**不是名册：名单 / 部门（含子孙）/ 标签的交集，发布时现算成
-- 定义里的 participants[]，每条带 ref = 联系人 id。这样名单增删改之后重新发布，
-- 下一版自然跟着变，不需要维护第二份快照；某一版实际邀请到了谁，由
-- contact_participation 逐条记着（那才是不可变的事实）。
--
-- 至少要写一个条件：没有条件就等于"全租户所有联系人"，一次误发布会给整个租户发邀请。
CREATE TABLE survey_audience (
    tenant_id           uuid        NOT NULL,
    survey_id           uuid        NOT NULL,
    list_id             uuid,
    org_unit_id         uuid,
    include_descendants boolean     NOT NULL DEFAULT true,
    tag                 text        CHECK (length(tag) BETWEEN 1 AND 64),
    updated_by          text        NOT NULL CHECK (length(updated_by) BETWEEN 1 AND 256),
    updated_at          timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, survey_id),
    FOREIGN KEY (tenant_id, survey_id) REFERENCES survey (tenant_id, id),
    FOREIGN KEY (tenant_id, list_id) REFERENCES contact_list (tenant_id, id),
    FOREIGN KEY (tenant_id, org_unit_id) REFERENCES contact_org_unit (tenant_id, id),
    CHECK (list_id IS NOT NULL OR org_unit_id IS NOT NULL OR tag IS NOT NULL)
);

ALTER TABLE survey_audience ENABLE ROW LEVEL SECURITY;
ALTER TABLE survey_audience FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON survey_audience
    USING (tenant_id = app_current_tenant()) WITH CHECK (tenant_id = app_current_tenant());
