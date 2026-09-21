-- 问卷路由（租户车道）：对外问卷 UUID ⇄ 引擎实例 ⇄ 引擎 sid，按租户隔离。
-- sid 只在实例内唯一（ADR 0002 决定 6），所以唯一键是 (实例, sid)，不是 sid。

CREATE TABLE survey_route (
    public_id           uuid        PRIMARY KEY,
    tenant_id           uuid        NOT NULL,
    engine_instance_id  text        NOT NULL,
    engine_sid          integer     NOT NULL CHECK (engine_sid > 0),
    created_at          timestamptz NOT NULL DEFAULT now(),
    -- 复合外键：路由只能指向本租户的实例。即便 tenant_id 写对了，指向别的租户的实例也会被拒绝。
    FOREIGN KEY (tenant_id, engine_instance_id) REFERENCES engine_instance (tenant_id, id),
    UNIQUE (engine_instance_id, engine_sid)
);

-- 路由一经登记不再改写、不删除（改指向应新建路由），运行期账号只有查询与新增。
REVOKE UPDATE, DELETE ON survey_route FROM platform_app;

ALTER TABLE survey_route ENABLE ROW LEVEL SECURITY;
ALTER TABLE survey_route FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON survey_route
    USING (tenant_id = app_current_tenant()) WITH CHECK (tenant_id = app_current_tenant());
