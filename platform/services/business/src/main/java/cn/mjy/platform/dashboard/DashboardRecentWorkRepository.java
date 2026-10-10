package cn.mjy.platform.dashboard;

import cn.mjy.platform.shared.TenantId;
import cn.mjy.platform.shared.tenant.TenantScope;
import java.sql.ResultSet;
import java.sql.SQLException;
import java.sql.Timestamp;
import java.time.Instant;
import java.util.List;
import java.util.Objects;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.stereotype.Repository;

@Repository
public class DashboardRecentWorkRepository {

    private static final int MAX_TARGETS = 50;

    private static final String UPSERT = """
            WITH RECURSIVE chain (id, parent_id, archived_at) AS (
                SELECT id, parent_id, archived_at
                FROM access_resource
                WHERE tenant_id = :tenant AND id = :survey AND kind = 'survey'
                UNION ALL
                SELECT parent.id, parent.parent_id, parent.archived_at
                FROM access_resource parent
                JOIN chain child ON parent.tenant_id = :tenant AND parent.id = child.parent_id
            ), effective_permissions AS (
                SELECT DISTINCT role_permission.permission_code
                FROM access_grant grant_row
                JOIN access_role_permission role_permission ON role_permission.role_code = grant_row.role_code
                WHERE grant_row.tenant_id = :tenant AND grant_row.actor_id = :actor
                  AND (grant_row.expires_at IS NULL OR grant_row.expires_at > now())
                  AND (grant_row.resource_id IS NULL OR grant_row.resource_id IN (SELECT id FROM chain))
            ), authorized AS (
                SELECT 1
                FROM access_member member
                WHERE member.tenant_id = :tenant AND member.actor_id = :actor AND member.status = 'active'
                  AND EXISTS (SELECT 1 FROM effective_permissions WHERE permission_code = 'view')
                  AND EXISTS (
                      SELECT 1 FROM effective_permissions
                      WHERE permission_code = CASE
                          WHEN :page IN ('edit', 'import', 'preview') THEN 'edit'
                          WHEN :page IN ('publish', 'version') THEN 'publish'
                          WHEN :page = 'responses' THEN 'view-statistics'
                      END
                  )
                  AND NOT EXISTS (SELECT 1 FROM chain WHERE archived_at IS NOT NULL)
            )
            INSERT INTO dashboard_recent_work
                (tenant_id, actor_id, survey_id, page, version_no, visited_at)
            SELECT :tenant, :actor, survey.id, :page, :version, :visited
            FROM survey
            WHERE survey.tenant_id = :tenant AND survey.id = :survey
              AND EXISTS (SELECT 1 FROM chain)
              AND EXISTS (SELECT 1 FROM authorized)
            ON CONFLICT (tenant_id, actor_id, survey_id, page, version_no) DO UPDATE
                SET visited_at = GREATEST(dashboard_recent_work.visited_at, EXCLUDED.visited_at)
            RETURNING true
            """;

    private static final String FIND_VISIBLE = """
            WITH RECURSIVE ancestors (survey_id, id, parent_id, archived_at) AS (
                SELECT resource.id, resource.id, resource.parent_id, resource.archived_at
                FROM access_resource resource
                JOIN dashboard_recent_work work
                  ON work.tenant_id = resource.tenant_id AND work.survey_id = resource.id
                WHERE resource.tenant_id = :tenant AND work.actor_id = :actor AND resource.kind = 'survey'
                UNION
                SELECT child.survey_id, parent.id, parent.parent_id, parent.archived_at
                FROM access_resource parent
                JOIN ancestors child ON parent.tenant_id = :tenant AND parent.id = child.parent_id
            ), effective_permissions AS (
                SELECT DISTINCT root.survey_id, role_permission.permission_code
                FROM (SELECT DISTINCT survey_id FROM ancestors) root
                JOIN access_grant grant_row
                  ON grant_row.tenant_id = :tenant AND grant_row.actor_id = :actor
                 AND (grant_row.expires_at IS NULL OR grant_row.expires_at > now())
                 AND (grant_row.resource_id IS NULL OR EXISTS (
                     SELECT 1 FROM ancestors scope
                     WHERE scope.survey_id = root.survey_id AND scope.id = grant_row.resource_id
                 ))
                JOIN access_role_permission role_permission ON role_permission.role_code = grant_row.role_code
            ), visible_work AS (
                SELECT work.*
                FROM dashboard_recent_work work
                JOIN access_member member
                  ON member.tenant_id = work.tenant_id AND member.actor_id = work.actor_id
                 AND member.status = 'active'
                WHERE work.tenant_id = :tenant AND work.actor_id = :actor
                  AND NOT EXISTS (
                      SELECT 1 FROM ancestors archived
                      WHERE archived.survey_id = work.survey_id AND archived.archived_at IS NOT NULL
                  )
                  AND EXISTS (
                      SELECT 1 FROM effective_permissions permission
                      WHERE permission.survey_id = work.survey_id AND permission.permission_code = 'view'
                  )
                  AND EXISTS (
                      SELECT 1 FROM effective_permissions permission
                      WHERE permission.survey_id = work.survey_id
                        AND permission.permission_code = CASE
                            WHEN work.page IN ('edit', 'import', 'preview') THEN 'edit'
                            WHEN work.page IN ('publish', 'version') THEN 'publish'
                            WHEN work.page = 'responses' THEN 'view-statistics'
                        END
                  )
            )
            SELECT work.survey_id, survey.title AS survey_name, work.page, work.version_no, work.visited_at
            FROM visible_work work
            JOIN survey ON survey.tenant_id = work.tenant_id AND survey.id = work.survey_id
            ORDER BY work.visited_at DESC, work.survey_id, work.page, work.version_no NULLS FIRST
            LIMIT :limit
            """;

    private final TenantScope tenantScope;
    private final JdbcClient jdbc;

    public DashboardRecentWorkRepository(TenantScope tenantScope, JdbcClient jdbc) {
        this.tenantScope = tenantScope;
        this.jdbc = jdbc;
    }

    public boolean upsert(TenantId tenantId, String actorId, RecentWorkCommand command, Instant now) {
        requireArguments(tenantId, actorId);
        Objects.requireNonNull(command, "command");
        Objects.requireNonNull(now, "now");
        return tenantScope.call(tenantId, () -> {
            boolean actorExists = jdbc.sql("""
                            SELECT actor_id FROM access_member
                            WHERE tenant_id = :tenant AND actor_id = :actor
                            FOR UPDATE
                            """)
                    .param("tenant", tenantId.value())
                    .param("actor", actorId)
                    .query(String.class)
                    .optional()
                    .isPresent();
            if (!actorExists) {
                return false;
            }
            boolean stored = jdbc.sql(UPSERT)
                    .param("tenant", tenantId.value())
                    .param("actor", actorId)
                    .param("survey", command.surveyId())
                    .param("page", command.page().code())
                    .param("version", command.version())
                    .param("visited", Timestamp.from(now))
                    .query(Boolean.class)
                    .optional()
                    .orElse(false);
            if (stored) {
                jdbc.sql("""
                                DELETE FROM dashboard_recent_work
                                WHERE ctid IN (
                                    SELECT ctid FROM dashboard_recent_work
                                    WHERE tenant_id = :tenant AND actor_id = :actor
                                    ORDER BY visited_at DESC, survey_id, page, version_no NULLS FIRST
                                    OFFSET :keep
                                )
                                """)
                        .param("tenant", tenantId.value())
                        .param("actor", actorId)
                        .param("keep", MAX_TARGETS)
                        .update();
            }
            return stored;
        });
    }

    public List<RecentWorkView> findVisible(TenantId tenantId, String actorId, int limit) {
        requireArguments(tenantId, actorId);
        if (limit < 1 || limit > MAX_TARGETS) {
            throw new IllegalArgumentException("limit must be between 1 and " + MAX_TARGETS);
        }
        return tenantScope.call(tenantId, () -> jdbc.sql(FIND_VISIBLE)
                .param("tenant", tenantId.value())
                .param("actor", actorId)
                .param("limit", limit)
                .query(DashboardRecentWorkRepository::map)
                .list());
    }

    private static RecentWorkView map(ResultSet row, int index) throws SQLException {
        java.util.UUID surveyId = row.getObject("survey_id", java.util.UUID.class);
        DashboardPage page = DashboardPage.fromCode(row.getString("page"));
        Integer version = (Integer) row.getObject("version_no");
        return new RecentWorkView(
                surveyId,
                row.getString("survey_name"),
                page,
                page.targetPath(surveyId, version),
                row.getTimestamp("visited_at").toInstant());
    }

    private static void requireArguments(TenantId tenantId, String actorId) {
        Objects.requireNonNull(tenantId, "tenantId");
        if (actorId == null || actorId.isBlank()) {
            throw new IllegalArgumentException("actorId is required");
        }
    }
}
