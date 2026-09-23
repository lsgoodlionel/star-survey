package cn.mjy.platform.contacts;

import java.sql.ResultSet;
import java.sql.SQLException;
import java.sql.Timestamp;
import java.sql.Types;
import java.time.Instant;
import java.util.List;
import java.util.Optional;
import java.util.UUID;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.stereotype.Repository;

/** 部门范围行的读写。须在 {@code TenantScope} 内调用。 */
@Repository
class ContactScopeRepository {

    private static final String COLUMNS = "id, actor_id, org_unit_id, expires_at";

    private final JdbcClient jdbc;

    ContactScopeRepository(JdbcClient jdbc) {
        this.jdbc = jdbc;
    }

    /** 同一人同一部门只有一行，重复授予只刷新到期时间与授予人（幂等）。 */
    ContactScopeView upsert(UUID id, String actorId, UUID orgUnitId, Instant expiresAt, String grantedBy) {
        return jdbc.sql("""
                        INSERT INTO contact_scope_grant (tenant_id, id, actor_id, org_unit_id, granted_by, expires_at)
                        VALUES (app_current_tenant(), :id, :actor, :unit, :by, :expires)
                        ON CONFLICT (tenant_id, actor_id, org_unit_id) DO UPDATE
                            SET expires_at = EXCLUDED.expires_at, granted_by = EXCLUDED.granted_by
                        RETURNING """ + " " + COLUMNS)
                .param("id", id).param("actor", actorId).param("unit", orgUnitId).param("by", grantedBy)
                .param("expires", expiresAt == null ? null : Timestamp.from(expiresAt), Types.TIMESTAMP)
                .query(ContactScopeRepository::toView).single();
    }

    Optional<ContactScopeView> find(UUID id) {
        return jdbc.sql("SELECT " + COLUMNS + " FROM contact_scope_grant WHERE id = :id")
                .param("id", id).query(ContactScopeRepository::toView).optional();
    }

    List<ContactScopeView> list() {
        return jdbc.sql("SELECT " + COLUMNS + " FROM contact_scope_grant ORDER BY actor_id, created_at")
                .query(ContactScopeRepository::toView).list();
    }

    void delete(UUID id) {
        jdbc.sql("DELETE FROM contact_scope_grant WHERE id = :id").param("id", id).update();
    }

    /** 离职即失权：撤掉此人的全部部门范围，返回撤掉的条数。 */
    int deleteAllFor(String actorId) {
        return jdbc.sql("DELETE FROM contact_scope_grant WHERE actor_id = :actor")
                .param("actor", actorId).update();
    }

    private static ContactScopeView toView(ResultSet rs, int row) throws SQLException {
        Timestamp expires = rs.getTimestamp("expires_at");
        return new ContactScopeView(rs.getObject("id", UUID.class), rs.getString("actor_id"),
                rs.getObject("org_unit_id", UUID.class), expires == null ? null : expires.toInstant());
    }
}
