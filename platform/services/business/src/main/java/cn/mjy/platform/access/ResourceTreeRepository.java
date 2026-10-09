package cn.mjy.platform.access;

import cn.mjy.platform.access.ResourceCursor.Position;
import cn.mjy.platform.shared.TenantId;
import java.sql.ResultSet;
import java.sql.SQLException;
import java.sql.Timestamp;
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

    private static final String COLUMNS = "id, kind, parent_id, name, created_at, updated_at, archived_at";

    /**
     * 调用者能看到的节点：从其生效的 view 授权所在节点向下展开（整租户授权即全部节点），
     * 再在授权集内按父节点、查询条件和稳定业务顺序分页。成员不在职时授权不生效。
     */
    private static final String VISIBLE_PREFIX = """
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
            ),
            archived_tree (id) AS (
                SELECT id FROM access_resource WHERE tenant_id = :tenant AND archived_at IS NOT NULL
                UNION
                SELECT c.id FROM access_resource c
                JOIN archived_tree a ON c.tenant_id = :tenant AND c.parent_id = a.id
            ),
            authorized AS MATERIALIZED (
                SELECT r.*, CASE r.kind WHEN 'project' THEN 0 WHEN 'folder' THEN 1 ELSE 2 END AS kind_rank
                FROM access_resource r
                WHERE r.tenant_id = :tenant AND r.id IN (SELECT id FROM visible)
                  AND (CAST(:parent AS uuid) IS NULL OR r.parent_id = CAST(:parent AS uuid))
            ),
            candidates AS (
                SELECT * FROM authorized
                WHERE (CAST(:kind AS text) IS NULL OR kind = CAST(:kind AS text))
                  AND (CAST(:query AS text) IS NULL OR name ILIKE '%' || CAST(:query AS text) || '%')
                  AND ((:archived = 'archived' AND archived_at IS NOT NULL)
                       OR (:archived = 'active' AND id NOT IN (SELECT id FROM archived_tree)))
            )
            """;

    private static final String UPDATED_DESC = VISIBLE_PREFIX + """
            SELECT id, kind, parent_id, name, created_at, updated_at, archived_at FROM candidates
            WHERE CAST(:afterId AS uuid) IS NULL
               OR kind_rank > :afterRank
               OR (kind_rank = :afterRank AND (
                    updated_at < CAST(:afterUpdated AS timestamptz)
                    OR (updated_at = CAST(:afterUpdated AS timestamptz) AND (
                        name COLLATE "C" > CAST(:afterName AS text) COLLATE "C"
                        OR (name COLLATE "C" = CAST(:afterName AS text) COLLATE "C"
                            AND id > CAST(:afterId AS uuid))))))
            ORDER BY kind_rank, updated_at DESC, name COLLATE "C", id
            LIMIT :limit
            """;

    private static final String NAME_ASC = VISIBLE_PREFIX + """
            SELECT id, kind, parent_id, name, created_at, updated_at, archived_at FROM candidates
            WHERE CAST(:afterId AS uuid) IS NULL
               OR kind_rank > :afterRank
               OR (kind_rank = :afterRank AND (
                    name COLLATE "C" > CAST(:afterName AS text) COLLATE "C"
                    OR (name COLLATE "C" = CAST(:afterName AS text) COLLATE "C"
                        AND id > CAST(:afterId AS uuid))))
            ORDER BY kind_rank, name COLLATE "C", id
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

    List<ResourceView> visible(TenantId tenant, String actorId, UUID parentId, ResourceListQuery query,
            Position after, int limit) {
        String sql = query.sort() == ResourceListQuery.Sort.UPDATED_DESC ? UPDATED_DESC : NAME_ASC;
        return jdbc.sql(sql)
                .param("view", Permission.VIEW.code())
                .param("tenant", tenant.value())
                .param("actor", actorId)
                .param("parent", parentId)
                .param("kind", query.kind() == null ? null : query.kind().code())
                .param("query", query.query())
                .param("archived", query.archived().code())
                .param("afterId", after == null ? null : after.id())
                .param("afterRank", after == null ? -1 : after.kindRank())
                .param("afterUpdated", after == null ? null : Timestamp.from(after.updatedAt()))
                .param("afterName", after == null ? null : after.name())
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
        jdbc.sql("UPDATE access_resource SET name = :name, updated_at = now() "
                        + "WHERE tenant_id = :tenant AND id = :id")
                .param("tenant", tenant.value())
                .param("id", id)
                .param("name", name)
                .update();
    }

    void reparent(TenantId tenant, UUID id, UUID parentId) {
        jdbc.sql("UPDATE access_resource SET parent_id = :parent, updated_at = now() "
                        + "WHERE tenant_id = :tenant AND id = :id")
                .param("tenant", tenant.value())
                .param("id", id)
                .param("parent", parentId)
                .update();
    }

    private static ResourceView toView(ResultSet rs, int row) throws SQLException {
        return new ResourceView(rs.getObject("id", UUID.class), rs.getString("kind"),
                rs.getObject("parent_id", UUID.class), rs.getString("name"),
                rs.getTimestamp("created_at").toInstant(), rs.getTimestamp("updated_at").toInstant(),
                rs.getTimestamp("archived_at") == null ? null : rs.getTimestamp("archived_at").toInstant());
    }
}
