-- 资源树支持改名与移动（ADR 0011 规则 2）后的数据库兜底。
-- 服务层在移动前锁住涉及的项目根行再检查祖先链，保证并发移动不成环；这里的触发器是纵深防御：
-- 即使有代码绕过服务层直接改 parent_id，也不能挂到问卷下、不能成环、不能改节点的类型 / 租户 / id。
-- 跨租户的父节点已由 V401 的复合外键 (tenant_id, parent_id) 挡住，行级安全让别的租户的行不可见。

CREATE FUNCTION access_resource_guard() RETURNS trigger
    LANGUAGE plpgsql
AS $$
DECLARE
    parent_kind text;
    ancestor    uuid;
    steps       integer := 0;
BEGIN
    IF TG_OP = 'UPDATE' AND (NEW.tenant_id <> OLD.tenant_id OR NEW.id <> OLD.id OR NEW.kind <> OLD.kind) THEN
        RAISE EXCEPTION 'access_resource: tenant_id, id and kind are immutable' USING ERRCODE = 'check_violation';
    END IF;
    IF NEW.parent_id IS NULL OR (TG_OP = 'UPDATE' AND NEW.parent_id IS NOT DISTINCT FROM OLD.parent_id) THEN
        RETURN NEW;
    END IF;

    SELECT kind INTO parent_kind FROM access_resource WHERE tenant_id = NEW.tenant_id AND id = NEW.parent_id;
    IF parent_kind = 'survey' THEN
        RAISE EXCEPTION 'access_resource: a survey cannot contain children' USING ERRCODE = 'check_violation';
    END IF;

    -- 沿新父节点向上走：遇到自己即成环。插入的新节点还没有子孙，不会成环，只需检查更新。
    IF TG_OP = 'UPDATE' THEN
        ancestor := NEW.parent_id;
        WHILE ancestor IS NOT NULL LOOP
            IF ancestor = NEW.id THEN
                RAISE EXCEPTION 'access_resource: moving % under its own descendant', NEW.id
                    USING ERRCODE = 'check_violation';
            END IF;
            steps := steps + 1;
            IF steps > 10000 THEN
                RAISE EXCEPTION 'access_resource: ancestor chain too deep' USING ERRCODE = 'check_violation';
            END IF;
            SELECT parent_id INTO ancestor FROM access_resource WHERE tenant_id = NEW.tenant_id AND id = ancestor;
        END LOOP;
    END IF;
    RETURN NEW;
END
$$;

CREATE TRIGGER access_resource_guard
    BEFORE INSERT OR UPDATE ON access_resource
    FOR EACH ROW EXECUTE FUNCTION access_resource_guard();

