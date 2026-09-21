-- 租户生命周期（租户车道）：provisioning -> active <-> suspended，任意非终态 -> closed（终态）。
-- 规则与 cn.mjy.platform.tenant.TenantStatus 一致；数据库触发器兜底，代码写错也改不出非法状态。

ALTER TABLE tenant DROP CONSTRAINT tenant_status_check;
ALTER TABLE tenant ADD CONSTRAINT tenant_status_check
    CHECK (status IN ('provisioning', 'active', 'suspended', 'closed'));

CREATE FUNCTION tenant_status_guard() RETURNS trigger
    LANGUAGE plpgsql
AS $$
BEGIN
    IF TG_OP = 'INSERT' THEN
        IF NEW.status <> 'provisioning' THEN
            RAISE EXCEPTION 'new tenants must start in provisioning, got %', NEW.status
                USING ERRCODE = 'check_violation';
        END IF;
        RETURN NEW;
    END IF;

    IF NEW.status IS DISTINCT FROM OLD.status AND NOT (
           (OLD.status = 'provisioning' AND NEW.status IN ('active', 'closed'))
        OR (OLD.status = 'active'       AND NEW.status IN ('suspended', 'closed'))
        OR (OLD.status = 'suspended'    AND NEW.status IN ('active', 'closed'))
    ) THEN
        RAISE EXCEPTION 'illegal tenant transition: % -> %', OLD.status, NEW.status
            USING ERRCODE = 'check_violation';
    END IF;
    IF OLD.status = 'closed' THEN
        RAISE EXCEPTION 'illegal tenant transition: closed is terminal'
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END
$$;

CREATE TRIGGER tenant_status_guard
    BEFORE INSERT OR UPDATE ON tenant
    FOR EACH ROW EXECUTE FUNCTION tenant_status_guard();

-- 控制平面最小授权：运行期账号可以新建租户、只能改状态列；不能删除，不能改编码与名称。
-- 谁能调用这些写操作由应用层的平台运营角色把关。
GRANT INSERT ON tenant TO platform_app;
GRANT UPDATE (status) ON tenant TO platform_app;
