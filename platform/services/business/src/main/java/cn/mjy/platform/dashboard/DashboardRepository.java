package cn.mjy.platform.dashboard;

import cn.mjy.platform.shared.TenantId;
import java.sql.Array;
import java.sql.ResultSet;
import java.sql.SQLException;
import java.sql.Timestamp;
import java.time.Instant;
import java.util.ArrayList;
import java.util.List;
import java.util.UUID;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.stereotype.Repository;

@Repository
public class DashboardRepository {

    private static final String AGGREGATE = """
            WITH RECURSIVE ancestors (survey_id, id, parent_id, archived_at) AS (
                SELECT resource.id, resource.id, resource.parent_id, resource.archived_at
                FROM access_resource resource
                WHERE resource.tenant_id = :tenant AND resource.kind = 'survey'
                UNION ALL
                SELECT child.survey_id, parent.id, parent.parent_id, parent.archived_at
                FROM access_resource parent
                JOIN ancestors child ON parent.tenant_id = :tenant AND parent.id = child.parent_id
            ), actor_permissions AS MATERIALIZED (
                SELECT grant_row.resource_id, role_permission.permission_code
                FROM access_member member
                JOIN access_grant grant_row
                  ON grant_row.tenant_id = member.tenant_id AND grant_row.actor_id = member.actor_id
                 AND (grant_row.expires_at IS NULL OR grant_row.expires_at > now())
                JOIN access_role_permission role_permission ON role_permission.role_code = grant_row.role_code
                WHERE member.tenant_id = :tenant AND member.actor_id = :actor AND member.status = 'active'
            ), active_surveys AS MATERIALIZED (
                SELECT DISTINCT root.survey_id
                FROM (SELECT DISTINCT survey_id FROM ancestors) root
                JOIN access_member member
                  ON member.tenant_id = :tenant AND member.actor_id = :actor AND member.status = 'active'
                WHERE NOT EXISTS (
                    SELECT 1 FROM ancestors archived
                    WHERE archived.survey_id = root.survey_id AND archived.archived_at IS NOT NULL
                )
            ), permissions AS MATERIALIZED (
                SELECT active.survey_id,
                       bool_or(actor_permission.permission_code = 'view') AS can_view,
                       bool_or(actor_permission.permission_code = 'edit') AS can_edit,
                       bool_or(actor_permission.permission_code = 'publish') AS can_publish,
                       bool_or(actor_permission.permission_code = 'approve-publish') AS can_approve,
                       bool_or(actor_permission.permission_code = 'view-statistics') AS can_statistics,
                       bool_or(actor_permission.permission_code = 'export-raw-responses') AS can_export
                FROM active_surveys active
                JOIN actor_permissions actor_permission
                  ON actor_permission.resource_id IS NULL OR EXISTS (
                     SELECT 1 FROM ancestors scope
                     WHERE scope.survey_id = active.survey_id AND scope.id = actor_permission.resource_id
                 )
                GROUP BY active.survey_id
            ), response_counts AS MATERIALIZED (
                SELECT version.survey_id, count(*) FILTER (WHERE response.state = 'engine_completed') AS completed
                FROM survey_published_version version
                JOIN response_projection response
                  ON response.engine_instance_id = version.engine_instance_id
                 AND response.survey_id = version.engine_sid
                WHERE version.tenant_id = :tenant
                GROUP BY version.survey_id
            ), open_approvals AS MATERIALIZED (
                SELECT DISTINCT ON (approval.survey_id)
                       approval.id, approval.survey_id, approval.status, approval.submitted_at
                FROM survey_publish_approval approval
                WHERE approval.tenant_id = :tenant AND approval.status IN ('pending', 'approved')
                ORDER BY approval.survey_id, approval.submitted_at DESC, approval.id
            ), survey_facts AS MATERIALIZED (
                SELECT survey.id, survey.title, survey.draft_version, survey.published_version,
                       survey.status, survey.updated_at, permission.can_edit, permission.can_publish,
                       permission.can_approve, permission.can_statistics, permission.can_export,
                       response.completed, approval.id AS approval_id, approval.status AS approval_status,
                       approval.submitted_at
                FROM survey
                JOIN permissions permission ON permission.survey_id = survey.id AND permission.can_view
                LEFT JOIN response_counts response ON response.survey_id = survey.id
                LEFT JOIN open_approvals approval ON approval.survey_id = survey.id
                WHERE survey.tenant_id = :tenant
            ), section_flags AS (
                SELECT ARRAY_REMOVE(ARRAY[
                    CASE WHEN EXISTS (
                        SELECT 1 FROM actor_permissions
                        WHERE resource_id IS NULL AND permission_code = 'approve-publish'
                    ) OR EXISTS (
                        SELECT 1 FROM permissions WHERE can_view AND can_approve
                    ) THEN 'approval' END,
                    CASE WHEN EXISTS (
                        SELECT 1 FROM actor_permissions
                        WHERE resource_id IS NULL AND permission_code = 'publish'
                    ) OR EXISTS (
                        SELECT 1 FROM permissions WHERE can_view AND can_publish
                    ) THEN 'publish' END,
                    CASE WHEN EXISTS (
                        SELECT 1 FROM actor_permissions
                        WHERE resource_id IS NULL AND permission_code = 'edit'
                    ) OR EXISTS (
                        SELECT 1 FROM permissions WHERE can_view AND can_edit
                    ) THEN 'preview' END,
                    CASE WHEN EXISTS (
                        SELECT 1 FROM actor_permissions
                        WHERE resource_id IS NULL AND permission_code = 'export-raw-responses'
                    ) OR EXISTS (
                        SELECT 1 FROM permissions WHERE can_view AND can_export
                    ) THEN 'export' END,
                    CASE WHEN EXISTS (
                        SELECT 1 FROM actor_permissions
                        WHERE resource_id IS NULL AND permission_code = 'view'
                    ) OR EXISTS (
                        SELECT 1 FROM permissions WHERE can_view
                    ) THEN 'survey' END
                ], NULL)::text[] AS visible_sections
            ), summary AS (
                SELECT
                    (SELECT count(*) FROM survey_facts WHERE can_approve AND approval_status = 'pending')
                        AS pending_approvals,
                    (SELECT count(*) FROM survey_facts
                     WHERE can_publish AND status IN ('publish_failed', 'pending_reconciliation'))
                        AS publish_exceptions,
                    (SELECT count(*) FROM survey_preview_session preview
                     JOIN survey_facts fact ON fact.id = preview.survey_id AND fact.can_edit
                     WHERE preview.status IN ('creating', 'ready', 'closing')) AS active_previews,
                    (SELECT count(*) FROM response_export_job export
                     JOIN survey_facts fact ON fact.id = export.survey_id AND fact.can_export
                     WHERE export.requested_by = :actor AND export.status IN ('queued', 'running')) AS active_exports,
                    EXISTS (SELECT 1 FROM access_member member
                            WHERE member.tenant_id = :tenant AND member.actor_id = :actor
                              AND member.status = 'active') AS active_member
            ), task_candidates AS (
                SELECT 2 AS severity, 'pending_approval:' || fact.approval_id AS task_key,
                       'pending_approval' AS task_kind, fact.id AS survey_id, fact.title AS survey_name,
                       '待审批' AS task_status, fact.submitted_at AS item_updated,
                       '/surveys/' || fact.id || '/publish' AS target_path
                FROM survey_facts fact WHERE fact.can_approve AND fact.approval_status = 'pending'
                UNION ALL
                SELECT 1, 'publish_exception:' || fact.id, 'publish_exception', fact.id, fact.title,
                       CASE fact.status WHEN 'publish_failed' THEN '发布失败' ELSE '发布待核对' END,
                       fact.updated_at, '/surveys/' || fact.id || '/publish'
                FROM survey_facts fact
                WHERE fact.can_publish AND fact.status IN ('publish_failed', 'pending_reconciliation')
                UNION ALL
                SELECT 3, 'preview_exception:' || preview.id, 'preview_exception', fact.id, fact.title,
                       CASE preview.status WHEN 'failed' THEN '预览创建失败' ELSE '预览关闭失败' END,
                       preview.updated_at, '/surveys/' || fact.id || '/preview'
                FROM survey_preview_session preview
                JOIN survey_facts fact ON fact.id = preview.survey_id AND fact.can_edit
                WHERE preview.status IN ('failed', 'cleanup_failed')
                UNION ALL
                SELECT 4, 'export_exception:' || export.id, 'export_exception', fact.id, fact.title,
                       CASE export.status WHEN 'failed' THEN '导出失败' ELSE '导出文件已过期' END,
                       COALESCE(export.finished_at, export.started_at, export.created_at),
                       '/surveys/' || fact.id || '/responses'
                FROM response_export_job export
                JOIN survey_facts fact ON fact.id = export.survey_id AND fact.can_export
                WHERE export.requested_by = :actor AND export.status IN ('failed', 'expired')
                UNION ALL
                SELECT 5, 'draft_pending_publish:' || fact.id, 'draft_pending_publish', fact.id, fact.title,
                       '待发布草稿', fact.updated_at, '/surveys/' || fact.id || '/publish'
                FROM survey_facts fact
                LEFT JOIN survey_published_version live
                  ON live.survey_id = fact.id AND live.version_no = fact.published_version
                WHERE fact.can_publish AND fact.status IN ('draft', 'published')
                  AND (live.draft_version IS NULL OR live.draft_version <> fact.draft_version)
            ), limited_tasks AS MATERIALIZED (
                SELECT * FROM task_candidates
                ORDER BY severity, item_updated DESC, task_key
                LIMIT :taskLimit
            ), limited_surveys AS MATERIALIZED (
                SELECT fact.*,
                       CASE
                           WHEN fact.status = 'publish_failed' THEN 'failed'
                           WHEN fact.status = 'pending_reconciliation' THEN 'needs_reconciliation'
                           WHEN fact.status = 'publishing' THEN 'publishing'
                           WHEN fact.approval_status = 'pending' THEN 'pending_approval'
                           WHEN fact.approval_status = 'approved' THEN 'approved'
                           ELSE fact.status
                       END AS publish_state,
                       (CASE WHEN fact.can_edit THEN ARRAY['edit', 'preview'] ELSE ARRAY[]::text[] END
                        || CASE WHEN fact.can_publish THEN ARRAY['publish'] ELSE ARRAY[]::text[] END
                        || CASE WHEN fact.can_statistics THEN ARRAY['responses'] ELSE ARRAY[]::text[] END) AS actions
                FROM survey_facts fact
                ORDER BY fact.updated_at DESC, fact.id
                LIMIT :surveyLimit
            )
            SELECT 0 AS row_group, 0 AS row_order, transaction_timestamp() AS generated_at,
                   flags.visible_sections, summary.pending_approvals, summary.publish_exceptions,
                   summary.active_previews, summary.active_exports, summary.active_member,
                   NULL::text AS task_key, NULL::text AS task_kind, NULL::uuid AS survey_id,
                   NULL::text AS survey_name, NULL::text AS task_status, NULL::timestamptz AS item_updated,
                   NULL::text AS target_path, NULL::integer AS draft_version,
                   NULL::integer AS published_version, NULL::text AS publish_state,
                   NULL::bigint AS completed_responses, NULL::text[] AS actions,
                   NULL::timestamptz AS sort_time, ''::text AS sort_key
            FROM section_flags flags CROSS JOIN summary
            UNION ALL
            SELECT 1, task.severity, NULL, NULL, NULL, NULL, NULL, NULL,
                   NULL, task.task_key, task.task_kind, task.survey_id, task.survey_name, task.task_status,
                   task.item_updated, task.target_path, NULL, NULL, NULL, NULL, NULL,
                   task.item_updated, task.task_key
            FROM limited_tasks task
            UNION ALL
            SELECT 2, 0, NULL, NULL, NULL, NULL, NULL, NULL,
                   NULL, NULL, NULL, survey.id, survey.title, NULL, survey.updated_at, NULL,
                   survey.draft_version, survey.published_version, survey.publish_state,
                   CASE WHEN survey.can_statistics THEN COALESCE(survey.completed, 0) END,
                   survey.actions, survey.updated_at, survey.id::text
            FROM limited_surveys survey
            ORDER BY row_group, row_order, sort_time DESC NULLS LAST, sort_key
            """;

    private final JdbcClient jdbc;
    public DashboardRepository(JdbcClient jdbc) {
        this.jdbc = jdbc;
    }

    Aggregate load(TenantId tenantId, String actorId, int surveyLimit, int taskLimit) {
        List<Row> rows = jdbc.sql(AGGREGATE)
                .param("tenant", tenantId.value())
                .param("actor", actorId)
                .param("surveyLimit", surveyLimit)
                .param("taskLimit", taskLimit)
                .query(DashboardRepository::map)
                .list();
        if (rows.isEmpty() || rows.getFirst().summary() == null) {
            throw new IllegalStateException("dashboard aggregate did not return its summary row");
        }
        Row summary = rows.getFirst();
        List<DashboardTaskView> tasks = new ArrayList<>();
        List<DashboardSurveyView> surveys = new ArrayList<>();
        rows.stream().map(Row::task).filter(java.util.Objects::nonNull).forEach(tasks::add);
        rows.stream().map(Row::survey).filter(java.util.Objects::nonNull).forEach(surveys::add);
        return new Aggregate(summary.generatedAt(), summary.visibleSections(), summary.summary(),
                summary.activeMember(), tasks, surveys);
    }

    private static Row map(ResultSet row, int index) throws SQLException {
        int group = row.getInt("row_group");
        if (group == 0) {
            return new Row(instant(row, "generated_at"), strings(row.getArray("visible_sections")),
                    new DashboardSummary(row.getLong("pending_approvals"), row.getLong("publish_exceptions"),
                            row.getLong("active_previews"), row.getLong("active_exports")),
                    row.getBoolean("active_member"), null, null);
        }
        UUID surveyId = row.getObject("survey_id", UUID.class);
        if (group == 1) {
            return new Row(null, List.of(), null, false,
                    new DashboardTaskView(row.getString("task_key"), row.getString("task_kind"), surveyId,
                            row.getString("survey_name"), row.getString("task_status"),
                            instant(row, "item_updated"), row.getString("target_path")), null);
        }
        return new Row(null, List.of(), null, false, null,
                new DashboardSurveyView(surveyId, row.getString("survey_name"), row.getInt("draft_version"),
                        (Integer) row.getObject("published_version"), row.getString("publish_state"),
                        (Long) row.getObject("completed_responses"), instant(row, "item_updated"),
                        strings(row.getArray("actions"))));
    }

    private static Instant instant(ResultSet row, String column) throws SQLException {
        Timestamp value = row.getTimestamp(column);
        return value == null ? null : value.toInstant();
    }

    private static List<String> strings(Array array) throws SQLException {
        if (array == null) {
            return List.of();
        }
        return List.of((String[]) array.getArray());
    }

    record Aggregate(Instant generatedAt, List<String> visibleSections, DashboardSummary summary, boolean activeMember,
            List<DashboardTaskView> tasks, List<DashboardSurveyView> surveys) {
    }

    private record Row(Instant generatedAt, List<String> visibleSections, DashboardSummary summary,
            boolean activeMember,
            DashboardTaskView task, DashboardSurveyView survey) {
    }

}
