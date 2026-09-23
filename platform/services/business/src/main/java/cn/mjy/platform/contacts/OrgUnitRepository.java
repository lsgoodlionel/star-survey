package cn.mjy.platform.contacts;

import java.sql.ResultSet;
import java.sql.SQLException;
import java.sql.Types;
import java.util.List;
import java.util.Optional;
import java.util.UUID;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.stereotype.Repository;

/** 部门树的读写与"可见部门集合"的计算。须在 {@code TenantScope} 内调用（行级安全负责租户隔离）。 */
@Repository
class OrgUnitRepository {

    /**
     * 调用者可见的部门：范围行指定的部门，加上它们的全部子孙。树无环（触发器兜底），递归必然终止。
     * 过期的范围行不参与。
     */
    static final String VISIBLE_UNITS = """
            WITH RECURSIVE visible_unit (id) AS (
                SELECT g.org_unit_id FROM contact_scope_grant g
                 WHERE g.actor_id = :actor AND (g.expires_at IS NULL OR g.expires_at > now())
                UNION
                SELECT u.id FROM contact_org_unit u JOIN visible_unit v ON u.parent_id = v.id
            )""";

    /** 指定部门及其子孙。 */
    static final String UNIT_SUBTREE = """
            unit_subtree (id) AS (
                SELECT :unit::uuid
                UNION
                SELECT u.id FROM contact_org_unit u JOIN unit_subtree s ON u.parent_id = s.id
            )""";

    private static final String COLUMNS = "id, parent_id, name, external_ref";

    private final JdbcClient jdbc;

    OrgUnitRepository(JdbcClient jdbc) {
        this.jdbc = jdbc;
    }

    OrgUnitView insert(UUID id, UUID parentId, String name, String externalRef, String createdBy) {
        return jdbc.sql("""
                        INSERT INTO contact_org_unit (tenant_id, id, parent_id, name, external_ref, created_by)
                        VALUES (app_current_tenant(), :id, :parent, :name, :ref, :by)
                        RETURNING """ + " " + COLUMNS)
                .param("id", id).param("parent", parentId).param("name", name)
                .param("ref", externalRef, Types.VARCHAR).param("by", createdBy)
                .query(OrgUnitRepository::toView).single();
    }

    Optional<OrgUnitView> find(UUID id) {
        return jdbc.sql("SELECT " + COLUMNS + " FROM contact_org_unit WHERE id = :id")
                .param("id", id).query(OrgUnitRepository::toView).optional();
    }

    Optional<OrgUnitView> findByExternalRef(String externalRef) {
        return jdbc.sql("SELECT " + COLUMNS + " FROM contact_org_unit WHERE external_ref = :ref")
                .param("ref", externalRef).query(OrgUnitRepository::toView).optional();
    }

    /** 该部门是否落在调用者的范围内；全租户范围时只要求它存在。 */
    boolean isVisible(ContactScope scope, UUID unitId) {
        if (unitId == null) {
            return false;
        }
        if (scope.wholeTenant()) {
            return find(unitId).isPresent();
        }
        return jdbc.sql(VISIBLE_UNITS + " SELECT count(*) FROM visible_unit WHERE id = :unit")
                .param("actor", scope.actorId()).param("unit", unitId)
                .query(Long.class).single() > 0;
    }

    List<OrgUnitView> list(ContactScope scope) {
        if (scope.wholeTenant()) {
            return jdbc.sql("SELECT " + COLUMNS + " FROM contact_org_unit ORDER BY name, id")
                    .query(OrgUnitRepository::toView).list();
        }
        return jdbc.sql(VISIBLE_UNITS + " SELECT " + COLUMNS + " FROM contact_org_unit"
                        + " WHERE id IN (SELECT id FROM visible_unit) ORDER BY name, id")
                .param("actor", scope.actorId()).query(OrgUnitRepository::toView).list();
    }

    /** 该部门是否是自己的子孙（含自身）：移动前的成环判定。 */
    boolean isDescendant(UUID candidate, UUID unitId) {
        return jdbc.sql("WITH RECURSIVE " + UNIT_SUBTREE
                        + " SELECT count(*) FROM unit_subtree WHERE id = :candidate")
                .param("unit", unitId).param("candidate", candidate)
                .query(Long.class).single() > 0;
    }

    void rename(UUID id, String name) {
        jdbc.sql("UPDATE contact_org_unit SET name = :name, updated_at = now() WHERE id = :id")
                .param("name", name).param("id", id).update();
    }

    void reparent(UUID id, UUID parentId) {
        jdbc.sql("UPDATE contact_org_unit SET parent_id = :parent, updated_at = now() WHERE id = :id")
                .param("parent", parentId).param("id", id).update();
    }

    /** 移动前锁住涉及的节点，防止并发移动绕过成环判定。 */
    void lock(List<UUID> ids) {
        if (ids.isEmpty()) {
            return;
        }
        jdbc.sql("SELECT id FROM contact_org_unit WHERE id IN (:ids) ORDER BY id FOR NO KEY UPDATE")
                .param("ids", ids).query(UUID.class).list();
    }

    private static OrgUnitView toView(ResultSet rs, int row) throws SQLException {
        return new OrgUnitView(rs.getObject("id", UUID.class), rs.getObject("parent_id", UUID.class),
                rs.getString("name"), rs.getString("external_ref"));
    }
}
