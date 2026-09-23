-- 联系人 ↔ 引擎参与者令牌的映射（WP-18 切片 18.1 的投放接点，ADR 0016 邀请码 / 引擎 tokens_<sid>）。
--
-- 引擎里每个已发布 sid 有一张 tokens_<sid> 表，一人一码。平台不在这里向引擎发码，也不负责发送：
-- 本表只记录"哪个联系人拿到了哪一版问卷的哪个令牌"，供投放车道取用与追踪（ADR 0017 决定 5）。
-- 令牌本身是凭据，按 ADR 0016"续答令牌不互用"：同一 (实例, sid) 下一个令牌只能属于一个联系人。
CREATE TABLE contact_participation (
    tenant_id          uuid        NOT NULL,
    id                 uuid        NOT NULL,
    contact_id         uuid        NOT NULL,
    survey_id          uuid        NOT NULL,
    version_no         integer     NOT NULL CHECK (version_no > 0),
    engine_instance_id text        NOT NULL CHECK (length(engine_instance_id) BETWEEN 1 AND 128),
    engine_sid         integer     NOT NULL CHECK (engine_sid > 0),
    participant_token  text        NOT NULL CHECK (participant_token ~ '^[A-Za-z0-9_-]{4,64}$'),
    issued_by          text        NOT NULL CHECK (length(issued_by) BETWEEN 1 AND 256),
    issued_at          timestamptz NOT NULL DEFAULT now(),
    revoked_by         text        CHECK (length(revoked_by) BETWEEN 1 AND 256),
    revoked_at         timestamptz,
    PRIMARY KEY (tenant_id, id),
    FOREIGN KEY (tenant_id, contact_id) REFERENCES contact (tenant_id, id),
    CHECK ((revoked_at IS NULL) = (revoked_by IS NULL)),
    CHECK (revoked_at IS NULL OR revoked_at >= issued_at)
);
-- 一个联系人在某一版问卷上同时只有一个有效令牌。
CREATE UNIQUE INDEX contact_participation_current
    ON contact_participation (tenant_id, contact_id, survey_id, version_no) WHERE revoked_at IS NULL;
-- 令牌不互用：同一引擎问卷下的同一令牌不会同时属于两个人。
CREATE UNIQUE INDEX contact_participation_token
    ON contact_participation (tenant_id, engine_instance_id, engine_sid, participant_token)
    WHERE revoked_at IS NULL;
CREATE INDEX contact_participation_survey ON contact_participation (tenant_id, survey_id, version_no);

REVOKE UPDATE, DELETE ON contact_participation FROM platform_app;
GRANT UPDATE (revoked_by, revoked_at) ON contact_participation TO platform_app;

ALTER TABLE contact_participation ENABLE ROW LEVEL SECURITY;
ALTER TABLE contact_participation FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON contact_participation
    USING (tenant_id = app_current_tenant()) WITH CHECK (tenant_id = app_current_tenant());
