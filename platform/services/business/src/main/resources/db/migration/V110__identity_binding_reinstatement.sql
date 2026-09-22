-- 撤销后恢复（管理员复职，ADR 0014 增补二）。
--
-- 原先每个主体至多一条撤销（主键 principal_id），恢复只能删行，而撤销表只追加。改为：
--   * 撤销行有自己的 id，同一主体可以多次"撤销 → 恢复 → 再撤销"，历史全部保留；
--   * 恢复 = 在撤销行上一次性盖上 reinstated_at / reinstated_by（运行期账号只能改这两列，触发器保证只能
--     从空改为非空、且只改一次，其余列不可变）；
--   * 同一主体同时至多一条未恢复的撤销（部分唯一索引），"是否已撤销" = 存在未恢复的撤销。
ALTER TABLE identity_binding_revocation
    ADD COLUMN id            uuid NOT NULL DEFAULT gen_random_uuid(),
    ADD COLUMN reinstated_at timestamptz,
    ADD COLUMN reinstated_by text CHECK (length(reinstated_by) BETWEEN 1 AND 256),
    ADD CONSTRAINT identity_binding_revocation_reinstated_together
        CHECK ((reinstated_at IS NULL) = (reinstated_by IS NULL)),
    ADD CONSTRAINT identity_binding_revocation_reinstated_after
        CHECK (reinstated_at IS NULL OR reinstated_at >= revoked_at);

ALTER TABLE identity_binding_revocation DROP CONSTRAINT identity_binding_revocation_pkey;
ALTER TABLE identity_binding_revocation
    ADD CONSTRAINT identity_binding_revocation_pkey PRIMARY KEY (id),
    ADD CONSTRAINT identity_binding_revocation_tenant_id_key UNIQUE (tenant_id, id);

CREATE UNIQUE INDEX identity_binding_revocation_open
    ON identity_binding_revocation (tenant_id, principal_id) WHERE reinstated_at IS NULL;
CREATE INDEX identity_binding_revocation_principal
    ON identity_binding_revocation (tenant_id, principal_id);

-- 仍然不能删除、不能改撤销本身；只能盖恢复戳。
GRANT UPDATE (reinstated_at, reinstated_by) ON identity_binding_revocation TO platform_app;

CREATE FUNCTION identity_binding_revocation_guard() RETURNS trigger
    LANGUAGE plpgsql AS $$
BEGIN
    IF OLD.reinstated_at IS NOT NULL THEN
        RAISE EXCEPTION 'revocation % is already reinstated', OLD.id USING ERRCODE = 'check_violation';
    END IF;
    IF NEW.reinstated_at IS NULL THEN
        RAISE EXCEPTION 'a revocation can only be reinstated' USING ERRCODE = 'check_violation';
    END IF;
    IF NEW.id <> OLD.id OR NEW.tenant_id <> OLD.tenant_id OR NEW.principal_id <> OLD.principal_id
            OR NEW.reason <> OLD.reason OR NEW.revoked_by <> OLD.revoked_by OR NEW.revoked_at <> OLD.revoked_at THEN
        RAISE EXCEPTION 'revocation facts are immutable' USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER identity_binding_revocation_guard
    BEFORE UPDATE ON identity_binding_revocation
    FOR EACH ROW EXECUTE FUNCTION identity_binding_revocation_guard();
