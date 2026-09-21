package cn.mjy.platform.access;

import cn.mjy.platform.shared.TenantId;
import java.sql.ResultSet;
import java.sql.SQLException;
import java.sql.Timestamp;
import java.time.Instant;
import java.util.List;
import java.util.Optional;
import java.util.UUID;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.stereotype.Repository;

/** 授权表的数据访问，以及"某成员在某资源上生效的权限"的继承计算。须在租户作用域内调用。 */
@Repository
class GrantRepository {

    static final String OWNER_ROLE = "tenant_owner";

    private static final String COLUMNS = "id, actor_id, role_code, resource_id, granted_by, expires_at";

    /**
     * 资源及其全部祖先（depth 0 为资源本身）。树无环，递归必然终止。
     * 生效授权 = 未过期，且范围为整个租户或落在这条祖先链上；成员须在职。
     */
    private static final String MATCHES_ON_RESOURCE = """
            WITH RECURSIVE chain (id, parent_id, depth) AS (
                SELECT id, parent_id, 0 FROM access_resource WHERE tenant_id = :tenant AND id = :resource
                UNION ALL
                SELECT r.id, r.parent_id, c.depth + 1
                FROM access_resource r JOIN chain c ON r.tenant_id = :tenant AND r.id = c.parent_id
            )
            SELECT rp.permission_code, g.role_code, g.resource_id, c.depth
            FROM access_grant g
            JOIN access_role_permission rp ON rp.role_code = g.role_code
            LEFT JOIN chain c ON c.id = g.resource_id
            WHERE g.tenant_id = :tenant AND g.actor_id = :actor
              AND (g.expires_at IS NULL OR g.expires_at > now())
              AND (g.resource_id IS NULL OR c.id IS NOT NULL)
            """;

    private static final String MATCHES_ON_TENANT = """
            SELECT rp.permission_code, g.role_code, g.resource_id, NULL::integer AS depth
            FROM access_grant g
            JOIN access_role_permission rp ON rp.role_code = g.role_code
            WHERE g.tenant_id = :tenant AND g.actor_id = :actor AND g.resource_id IS NULL
              AND (g.expires_at IS NULL OR g.expires_at > now())
            """;

    private final JdbcClient jdbc;

    GrantRepository(JdbcClient jdbc) {
        this.jdbc = jdbc;
    }

    /** 覆盖该资源（为空时指租户本身）的全部授权-权限组合。 */
    List<GrantMatch> matches(TenantId tenant, String actorId, UUID resourceId) {
        JdbcClient.StatementSpec statement = resourceId == null
                ? jdbc.sql(MATCHES_ON_TENANT)
                : jdbc.sql(MATCHES_ON_RESOURCE).param("resource", resourceId);
        return statement
                .param("tenant", tenant.value())
                .param("actor", actorId)
                .query((rs, n) -> new GrantMatch(
                        RoleCatalogue.toPermission(rs.getString("permission_code")),
                        rs.getString("role_code"),
                        rs.getObject("resource_id", UUID.class),
                        (Integer) rs.getObject("depth")))
                .list();
    }

    /** 新建授权；同一成员、角色、范围已存在时只更新到期时间与授权人，返回原 id（幂等）。 */
    UUID upsert(TenantId tenant, GrantRequest request, String grantedBy) {
        return jdbc.sql("""
                INSERT INTO access_grant (tenant_id, id, actor_id, role_code, resource_id, granted_by, expires_at)
                VALUES (:tenant, :id, :actor, :role, :resource, :by, :expires)
                ON CONFLICT (tenant_id, actor_id, role_code, resource_id) DO UPDATE
                    SET expires_at = EXCLUDED.expires_at, granted_by = EXCLUDED.granted_by
                RETURNING id
                """)
                .param("tenant", tenant.value())
                .param("id", UUID.randomUUID())
                .param("actor", request.actorId())
                .param("role", request.roleCode())
                .param("resource", request.resourceId())
                .param("by", grantedBy)
                .param("expires", request.expiresAt() == null ? null : Timestamp.from(request.expiresAt()))
                .query(UUID.class)
                .single();
    }

    Optional<GrantView> find(TenantId tenant, UUID grantId) {
        return jdbc.sql("SELECT " + COLUMNS + " FROM access_grant WHERE tenant_id = :tenant AND id = :id")
                .param("tenant", tenant.value())
                .param("id", grantId)
                .query(GrantRepository::toView)
                .optional();
    }

    List<GrantView> list(TenantId tenant) {
        return jdbc.sql("SELECT " + COLUMNS + " FROM access_grant WHERE tenant_id = :tenant ORDER BY created_at, id")
                .param("tenant", tenant.value())
                .query(GrantRepository::toView)
                .list();
    }

    List<GrantView> listForActor(TenantId tenant, String actorId) {
        return jdbc.sql("SELECT " + COLUMNS + " FROM access_grant WHERE tenant_id = :tenant AND actor_id = :actor")
                .param("tenant", tenant.value())
                .param("actor", actorId)
                .query(GrantRepository::toView)
                .list();
    }

    void delete(TenantId tenant, UUID grantId) {
        jdbc.sql("DELETE FROM access_grant WHERE tenant_id = :tenant AND id = :id")
                .param("tenant", tenant.value())
                .param("id", grantId)
                .update();
    }

    /** 在职、未过期的租户所有者人数（整租户范围的 tenant_owner 授权）。 */
    long activeOwners(TenantId tenant) {
        return jdbc.sql("""
                SELECT COUNT(DISTINCT g.actor_id) FROM access_grant g
                JOIN access_member m ON m.tenant_id = g.tenant_id AND m.actor_id = g.actor_id
                WHERE g.tenant_id = :tenant AND g.role_code = :owner AND g.resource_id IS NULL
                  AND m.status = 'active' AND (g.expires_at IS NULL OR g.expires_at > now())
                """)
                .param("tenant", tenant.value())
                .param("owner", OWNER_ROLE)
                .query(Long.class)
                .single();
    }

    private static GrantView toView(ResultSet rs, int row) throws SQLException {
        Timestamp expires = rs.getTimestamp("expires_at");
        Instant expiresAt = expires == null ? null : expires.toInstant();
        return new GrantView(rs.getObject("id", UUID.class), rs.getString("actor_id"), rs.getString("role_code"),
                rs.getObject("resource_id", UUID.class), rs.getString("granted_by"), expiresAt);
    }
}
