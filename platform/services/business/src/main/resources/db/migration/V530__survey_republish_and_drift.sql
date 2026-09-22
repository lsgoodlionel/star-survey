-- 重新发布与漂移检测（survey 车道 V530–V539，ADR 0012）。只引用已发布的旧迁移（V102、V500）。
--
-- 1. 公开路由可以切换：survey_route 从"一个公开 UUID 一条路由"改为"一个公开 UUID 若干条路由、恰一条当前"。
--    这张表属于租户模块（V102）；切换路由是重新发布的核心，改动放在本车道的号段里并经 ADR 0012 说明，
--    运行期仍只通过租户模块的 SurveyRouteService 读写它。
--    - 主键改为 (engine_instance_id, engine_sid)：每个引擎问卷永远只属于一个公开 UUID，旧 sid 的答卷
--      （引擎事件只带实例与 sid）在切换之后仍能反查到公开 UUID；
--    - superseded_at 为空的是当前路由，部分唯一索引保证每个公开 UUID 至多一条；
--    - 运行期账号只多了一项权限：UPDATE (superseded_at)。路由指向（实例、sid、公开 UUID）仍不可改写、不可删除，
--      触发器保证被取代的路由不能"复活"、取代时刻不能改。
ALTER TABLE survey_route ADD COLUMN superseded_at timestamptz;
ALTER TABLE survey_route DROP CONSTRAINT survey_route_pkey;
ALTER TABLE survey_route DROP CONSTRAINT survey_route_engine_instance_id_engine_sid_key;
ALTER TABLE survey_route ADD PRIMARY KEY (engine_instance_id, engine_sid);
CREATE UNIQUE INDEX survey_route_current ON survey_route (public_id) WHERE superseded_at IS NULL;
CREATE INDEX survey_route_public_id ON survey_route (public_id);
GRANT UPDATE (superseded_at) ON survey_route TO platform_app;

CREATE FUNCTION survey_route_supersede_once() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF OLD.superseded_at IS NOT NULL THEN
        RAISE EXCEPTION 'survey route (%, %) is already superseded', OLD.engine_instance_id, OLD.engine_sid;
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER survey_route_supersede_once BEFORE UPDATE ON survey_route
    FOR EACH ROW EXECUTE FUNCTION survey_route_supersede_once();

-- 2. 状态机：published 必有在线版本。publishing / publish_failed / pending_reconciliation 搭配非空的
--    published_version，就是"第 N 版在线、第 N+1 版在途"这一子状态（见 SurveyStatus）。
ALTER TABLE survey ADD CONSTRAINT survey_published_has_version
    CHECK (status <> 'published' OR published_version IS NOT NULL);

-- 3. 被取代的版本：路由切走的那一刻写入（与新版本、路由切换同一事务），随后由网关收口（设过期）。
--    survey_published_version 只追加不可改，收口进度单独记在这里；行不删除。
CREATE TABLE survey_version_retirement (
    tenant_id             uuid        NOT NULL,
    survey_id             uuid        NOT NULL,
    version_no            integer     NOT NULL,
    superseded_by_version integer     NOT NULL,
    engine_instance_id    text        NOT NULL,
    engine_sid            integer     NOT NULL CHECK (engine_sid > 0),
    superseded_at         timestamptz NOT NULL DEFAULT now(),
    close_attempts        integer     NOT NULL DEFAULT 0 CHECK (close_attempts >= 0),
    next_close_at         timestamptz,
    last_close_error      text,
    -- 网关确认引擎已过期的时刻与引擎里的过期值；为空表示尚未收口（旧 sid 仍可直接作答）。
    engine_closed_at      timestamptz,
    engine_expires        text,
    PRIMARY KEY (tenant_id, survey_id, version_no),
    CHECK (superseded_by_version > version_no),
    FOREIGN KEY (tenant_id, survey_id, version_no)
        REFERENCES survey_published_version (tenant_id, survey_id, version_no),
    FOREIGN KEY (tenant_id, survey_id, superseded_by_version)
        REFERENCES survey_published_version (tenant_id, survey_id, version_no)
);
CREATE INDEX survey_version_retirement_open
    ON survey_version_retirement (tenant_id, next_close_at) WHERE engine_closed_at IS NULL;

-- 4. 漂移检查记录：每次检查一行，只追加。outcome：match / drift / error（引擎读不出来，结论未知）。
CREATE TABLE survey_drift_check (
    tenant_id            uuid        NOT NULL,
    id                   bigint      GENERATED ALWAYS AS IDENTITY,
    survey_id            uuid        NOT NULL,
    version_no           integer     NOT NULL,
    engine_instance_id   text        NOT NULL,
    engine_sid           integer     NOT NULL,
    expected_fingerprint text        NOT NULL,
    current_fingerprint  text,
    outcome              text        NOT NULL CHECK (outcome IN ('match', 'drift', 'error')),
    issues               jsonb       NOT NULL DEFAULT '[]'::jsonb,
    renamed              jsonb       NOT NULL DEFAULT '[]'::jsonb,
    detail               text,
    checked_by           text        NOT NULL,
    checked_at           timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, id),
    FOREIGN KEY (tenant_id, survey_id, version_no)
        REFERENCES survey_published_version (tenant_id, survey_id, version_no)
);
CREATE INDEX survey_drift_check_version ON survey_drift_check (tenant_id, survey_id, version_no, id);

REVOKE DELETE ON survey_version_retirement FROM platform_app;
REVOKE UPDATE, DELETE ON survey_drift_check FROM platform_app;

ALTER TABLE survey_version_retirement ENABLE ROW LEVEL SECURITY;
ALTER TABLE survey_version_retirement FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON survey_version_retirement
    USING (tenant_id = app_current_tenant()) WITH CHECK (tenant_id = app_current_tenant());

ALTER TABLE survey_drift_check ENABLE ROW LEVEL SECURITY;
ALTER TABLE survey_drift_check FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON survey_drift_check
    USING (tenant_id = app_current_tenant()) WITH CHECK (tenant_id = app_current_tenant());
