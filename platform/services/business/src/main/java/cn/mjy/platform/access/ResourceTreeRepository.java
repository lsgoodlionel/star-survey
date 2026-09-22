package cn.mjy.platform.access;

import cn.mjy.platform.shared.TenantId;
import java.sql.ResultSet;
import java.sql.SQLException;
import java.util.Collection;
import java.util.List;
import java.util.Optional;
import java.util.UUID;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.stereotype.Repository;

/**
 * 资源树的查询与结构变更（改名、改父节点、锁根行）。须在租户作用域内调用；
 * 显式带 tenant_id 只是纵深防御，隔离靠行级安全。插入仍走 {@link AccessResources#register}。
 */
@Repository
class ResourceTreeRepository {

    private static final String COLUMNS = "id, kind, parent_id, name, created_at";

    /**
     * 调用者能看到的节点：从其生效的 view 授权所在节点向下展开（整租户授权即全部节点），
     * 再按父节点与游标过滤，按 id 排序分页。成员不在职时授权不生效。
     */
    private static final String VISIBLE = """
            WITH RECURSIVE live AS (
                SELECT g.resource_id
                FROM access_grant g
                JOIN access_role_permission rp ON rp.role_code = g.role_code AND rp.permission_code = :view
                JOIN access_member m ON m.tenant_id = g.tenant_id AND m.actor_id = g.actor_id AND m.status = 'active'
                WHERE g.tenant_id = :tenant AND g.actor_id = :actor
                  AND (g.expires_at IS NULL OR g.expires_at > now())
            ),
            visible (id) AS (
                SELECT r.id FROM access_resource r
                WHERE r.tenant_id = :tenant
                  AND (r.id IN (SELECT resource_id FROM live WHERE resource_id IS NOT NULL)
                       OR EXISTS (SELECT 1 FROM live WHERE resource_id IS NULL))
                UNION
                SELECT c.id FROM access_resource c JOIN visible v ON c.tenant_id = :tenant AND c.parent_id = v.id
            )
            SELECT id, kind, parent_id, name, created_at FROM access_resource
            WHERE tenant_id = :tenant AND id IN (SELECT id FROM visible)
              AND (CAST(:parent AS uuid) IS NULL OR parent_id = CAST(:parent AS uuid))
              AND (CAST(:after AS uuid) IS NULL OR id > CAST(:after AS uuid))
            ORDER BY id
            LIMIT :limit
            """;

    /** 节点及其全部祖先，从节点自身（depth 0）向上。树无环，递归必然终止。 */
    private static final String CHAIN = """
            WITH RECURSIVE chain (id, parent_id, depth) AS (
                SELECT id, parent_id, 0 FROM access_resource WHERE tenant_id = :tenant AND id = :id
                UNION ALL
                SELECT r.id, r.parent_id, c.depth + 1
                FROM access_resource r JOIN chain c ON r.tenant_id = :tenant AND r.id = c.parent_id
            )
            SELECT id FROM chain ORDER BY depth
            """;

    private final JdbcClient jdbc;

    ResourceTreeRepository(JdbcClient jdbc) {
        this.jdbc = jdbc;
    }

    Optional<ResourceView> find(TenantId tenant, UUID id) {
        return jdbc.sql("SELECT " + COLUMNS + " FROM access_resource WHERE tenant_id = :tenant AND id = :id")
                .param("tenant", tenant.value())
                .param("id", id)
                .query(ResourceTreeRepository::toView)
                .optional();
    }

    List<ResourceView> visible(TenantId tenant, String actorId, UUID parentId, UUID afterId, int limit) {
        return jdbc.sql(VISIBLE)
                .param("view", Permission.VIEW.code())
                .param("tenant", tenant.value())
                .param("actor", actorId)
                .param("parent", parentId)
                .param("after", afterId)
                .param("limit", limit)
                .query(ResourceTreeRepository::toView)
                .list();
    }

    /** 节点自身与全部祖先的 id，从节点向上；最后一个是所在项目。节点不存在时为空。 */
    List<UUID> chain(TenantId tenant, UUID id) {
        return jdbc.sql(CHAIN)
                .param("tenant", tenant.value())
                .param("id", id)
                .query(UUID.class)
                .list();
    }

    /**
     * 按 id 顺序给这些行加 FOR NO KEY UPDATE 锁（固定顺序避免死锁）。
     * 不与外键检查用的 KEY SHARE 冲突，所以锁住项目时仍可在其下新建节点。
     */
    void lock(TenantId tenant, Collection<UUID> ids) {
        jdbc.sql("""
                SELECT id FROM access_resource WHERE tenant_id = :tenant AND id IN (:ids)
                ORDER BY id FOR NO KEY UPDATE
                """)
                .param("tenant", tenant.value())
                .param("ids", List.copyOf(ids))
                .query(UUID.class)
                .list();
    }

    void rename(TenantId tenant, UUID id, String name) {
        jdbc.sql("UPDATE access_resource SET name = :name WHERE tenant_id = :tenant AND id = :id")
                .param("tenant", tenant.value())
                .param("id", id)
                .param("name", name)
                .update();
    }

    void reparent(TenantId tenant, UUID id, UUID parentId) {
        jdbc.sql("UPDATE access_resource SET parent_id = :parent WHERE tenant_id = :tenant AND id = :id")
                .param("tenant", tenant.value())
                .param("id", id)
                .param("parent", parentId)
                .update();
    }

    private static ResourceView toView(ResultSet rs, int row) throws SQLException {
        return new ResourceView(rs.getObject("id", UUID.class), rs.getString("kind"),
                rs.getObject("parent_id", UUID.class), rs.getString("name"),
                rs.getTimestamp("created_at").toInstant());
    }
}
