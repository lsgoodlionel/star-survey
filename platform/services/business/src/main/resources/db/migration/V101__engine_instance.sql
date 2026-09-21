-- 引擎实例登记（租户车道）。一个租户拥有一套或多套引擎实例（ADR 0002：一个租户 = 独立的引擎实例），
-- 一个实例只属于一个租户。实例标识全局唯一，引擎事件据此反查租户。

CREATE TABLE engine_instance (
    id          text        PRIMARY KEY CHECK (id ~ '^[a-z0-9][a-z0-9-]{1,62}$'),
    tenant_id   uuid        NOT NULL REFERENCES tenant (id),
    base_url    text        NOT NULL CHECK (base_url ~ '^https?://'),
    status      text        NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'draining', 'retired')),
    created_at  timestamptz NOT NULL DEFAULT now(),
    -- 供问卷路由做 (tenant_id, engine_instance_id) 复合外键：路由只能指向本租户的实例。
    UNIQUE (tenant_id, id)
);

-- 最小授权：登记用 INSERT，状态变更只能改 status 列；实例下线用 retired，不删除。
REVOKE UPDATE, DELETE ON engine_instance FROM platform_app;
GRANT UPDATE (status) ON engine_instance TO platform_app;

ALTER TABLE engine_instance ENABLE ROW LEVEL SECURITY;
ALTER TABLE engine_instance FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON engine_instance
    USING (tenant_id = app_current_tenant()) WITH CHECK (tenant_id = app_current_tenant());

-- 归属查询（EngineInstanceDirectory）：引擎事件到达时还不知道租户，需要跨租户按实例标识反查。
-- 不给运行期账号开放整表，而是只开放一个 SECURITY DEFINER 函数：输入一个实例标识，
-- 只在"实例 active 且租户 active"时返回租户，否则返回 NULL。函数以本迁移的执行者（表所有者）身份运行，
-- 下面这条只读策略只对该角色生效，让函数在所有者不是超级用户时同样能读到行；运行期账号不受影响。
CREATE POLICY directory_lookup ON engine_instance FOR SELECT TO CURRENT_USER USING (true);

CREATE FUNCTION engine_instance_tenant(p_instance_id text) RETURNS uuid
    LANGUAGE sql STABLE SECURITY DEFINER
    SET search_path = pg_catalog, pg_temp
AS $$
    SELECT e.tenant_id
      FROM public.engine_instance e
      JOIN public.tenant t ON t.id = e.tenant_id
     WHERE e.id = p_instance_id
       AND e.status = 'active'
       AND t.status = 'active'
$$;
REVOKE ALL ON FUNCTION engine_instance_tenant(text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION engine_instance_tenant(text) TO platform_app;
