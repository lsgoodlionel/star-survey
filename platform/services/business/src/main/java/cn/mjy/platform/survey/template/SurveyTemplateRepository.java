package cn.mjy.platform.survey.template;

import cn.mjy.platform.shared.TenantId;
import java.sql.ResultSet;
import java.sql.SQLException;
import java.sql.Types;
import java.time.Instant;
import java.util.List;
import java.util.Optional;
import java.util.UUID;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.stereotype.Repository;

/**
 * 租户模板三张表（V540）的读写。必须在 {@code TenantScope} 内调用：查询不带 tenant_id 条件，
 * 可见范围完全由行级安全决定，所以别的租户的 id 查出来就是"没有"。
 *
 * <p>状态迁移一律写成带 from 状态谓词的 UPDATE：空结果即"当前状态不允许"，
 * 不需要先读再判，也就不存在两次之间被别人改掉的窗口。
 */
@Repository
class SurveyTemplateRepository {

    private static final String COLUMNS = """
            id, name, description, status, current_version, published_version, created_by,
            reviewed_by, reviewed_at, review_reason, updated_at""";

    private final JdbcClient jdbc;

    SurveyTemplateRepository(JdbcClient jdbc) {
        this.jdbc = jdbc;
    }

    void insert(TenantId tenant, UUID id, String name, String description, String createdBy) {
        jdbc.sql("""
                        INSERT INTO survey_template
                            (tenant_id, id, name, description, status, current_version, created_by)
                        VALUES (:tenant, :id, :name, :description, 'draft', 1, :createdBy)
                        """)
                .param("tenant", tenant.value())
                .param("id", id)
                .param("name", name)
                .param("description", description, Types.VARCHAR)
                .param("createdBy", createdBy)
                .update();
    }

    Optional<TemplateView> find(UUID id) {
        return jdbc.sql("SELECT " + COLUMNS + " FROM survey_template WHERE id = :id")
                .param("id", id)
                .query(SurveyTemplateRepository::map)
                .optional();
    }

    /** 取行锁：同一模板的并发审核与复制在这里串行化。 */
    Optional<TemplateView> lock(UUID id) {
        return jdbc.sql("SELECT " + COLUMNS + " FROM survey_template WHERE id = :id FOR UPDATE")
                .param("id", id)
                .query(SurveyTemplateRepository::map)
                .optional();
    }

    /** 模板库：只有上架的模板。 */
    List<TemplateView> published() {
        return jdbc.sql("SELECT " + COLUMNS
                        + " FROM survey_template WHERE status = 'published' ORDER BY updated_at DESC, id")
                .query(SurveyTemplateRepository::map)
                .list();
    }

    /** 审核队列：本租户所有待审模板。 */
    List<TemplateView> pending() {
        return jdbc.sql("SELECT " + COLUMNS
                        + " FROM survey_template WHERE status = 'pending' ORDER BY updated_at, id")
                .query(SurveyTemplateRepository::map)
                .list();
    }

    Optional<TemplateView> submit(UUID id) {
        return jdbc.sql("""
                        UPDATE survey_template
                           SET status = 'pending', published_version = NULL, updated_at = now()
                         WHERE id = :id AND status IN ('draft', 'rejected', 'unpublished', 'published')
                        RETURNING """ + " " + COLUMNS)
                .param("id", id)
                .query(SurveyTemplateRepository::map)
                .optional();
    }

    Optional<TemplateView> approve(UUID id, String reviewer) {
        return jdbc.sql("""
                        UPDATE survey_template
                           SET status = 'published', published_version = current_version,
                               reviewed_by = :reviewer, reviewed_at = now(), review_reason = NULL,
                               updated_at = now()
                         WHERE id = :id AND status = 'pending'
                        RETURNING """ + " " + COLUMNS)
                .param("id", id)
                .param("reviewer", reviewer)
                .query(SurveyTemplateRepository::map)
                .optional();
    }

    Optional<TemplateView> reject(UUID id, String reviewer, String reason) {
        return jdbc.sql("""
                        UPDATE survey_template
                           SET status = 'rejected', published_version = NULL, reviewed_by = :reviewer,
                               reviewed_at = now(), review_reason = :reason, updated_at = now()
                         WHERE id = :id AND status = 'pending'
                        RETURNING """ + " " + COLUMNS)
                .param("id", id)
                .param("reviewer", reviewer)
                .param("reason", reason)
                .query(SurveyTemplateRepository::map)
                .optional();
    }

    Optional<TemplateView> unpublish(UUID id, String actor, String reason) {
        return jdbc.sql("""
                        UPDATE survey_template
                           SET status = 'unpublished', published_version = NULL, reviewed_by = :actor,
                               reviewed_at = now(), review_reason = :reason, updated_at = now()
                         WHERE id = :id AND status = 'published'
                        RETURNING """ + " " + COLUMNS)
                .param("id", id)
                .param("actor", actor)
                .param("reason", reason, Types.VARCHAR)
                .query(SurveyTemplateRepository::map)
                .optional();
    }

    /** 加一个版本号并返回它；调用方随后写入该版本的快照。 */
    int nextVersion(UUID id) {
        return jdbc.sql("""
                        UPDATE survey_template SET current_version = current_version + 1, updated_at = now()
                         WHERE id = :id
                        RETURNING current_version
                        """)
                .param("id", id)
                .query(Integer.class)
                .single();
    }

    // ------------------------------------------------------------ 版本快照

    void insertVersion(TenantId tenant, UUID templateId, int versionNo, String definition,
            UUID sourceSurveyId, Integer sourceDraftVersion, String createdBy) {
        jdbc.sql("""
                        INSERT INTO survey_template_version (tenant_id, template_id, version_no, definition,
                            source_survey_id, source_draft_version, created_by)
                        VALUES (:tenant, :template, :version, CAST(:definition AS jsonb),
                            CAST(:source AS uuid), :draftVersion, :createdBy)
                        """)
                .param("tenant", tenant.value())
                .param("template", templateId)
                .param("version", versionNo)
                .param("definition", definition)
                .param("source", sourceSurveyId, Types.OTHER)
                .param("draftVersion", sourceDraftVersion, Types.INTEGER)
                .param("createdBy", createdBy)
                .update();
    }

    Optional<String> definition(UUID templateId, int versionNo) {
        return jdbc.sql("""
                        SELECT definition::text FROM survey_template_version
                         WHERE template_id = :template AND version_no = :version
                        """)
                .param("template", templateId)
                .param("version", versionNo)
                .query(String.class)
                .optional();
    }

    List<TemplateVersionView> versions(UUID templateId) {
        return jdbc.sql("""
                        SELECT version_no, source_survey_id, source_draft_version, created_by, created_at
                          FROM survey_template_version WHERE template_id = :template ORDER BY version_no
                        """)
                .param("template", templateId)
                .query((rs, row) -> new TemplateVersionView(
                        rs.getInt("version_no"),
                        rs.getObject("source_survey_id", UUID.class),
                        (Integer) rs.getObject("source_draft_version"),
                        rs.getString("created_by"),
                        instant(rs, "created_at")))
                .list();
    }

    // ------------------------------------------------------------ 动作历史

    void appendEvent(TenantId tenant, UUID templateId, String event, String actor, Integer versionNo,
            String reason, UUID copiedSurveyId) {
        jdbc.sql("""
                        INSERT INTO survey_template_event (tenant_id, template_id, event, actor, version_no,
                            reason, copied_survey_id)
                        VALUES (:tenant, :template, :event, :actor, :version, :reason, CAST(:copied AS uuid))
                        """)
                .param("tenant", tenant.value())
                .param("template", templateId)
                .param("event", event)
                .param("actor", actor)
                .param("version", versionNo, Types.INTEGER)
                .param("reason", reason, Types.VARCHAR)
                .param("copied", copiedSurveyId, Types.OTHER)
                .update();
    }

    List<TemplateEventView> history(UUID templateId) {
        return jdbc.sql("""
                        SELECT event, actor, version_no, reason, copied_survey_id, occurred_at
                          FROM survey_template_event WHERE template_id = :template ORDER BY id
                        """)
                .param("template", templateId)
                .query((rs, row) -> new TemplateEventView(
                        rs.getString("event"),
                        rs.getString("actor"),
                        (Integer) rs.getObject("version_no"),
                        rs.getString("reason"),
                        rs.getObject("copied_survey_id", UUID.class),
                        instant(rs, "occurred_at")))
                .list();
    }

    // ------------------------------------------------------------ 映射

    private static TemplateView map(ResultSet rs, int row) throws SQLException {
        return new TemplateView(
                rs.getObject("id", UUID.class),
                TemplateScope.TENANT,
                rs.getString("name"),
                rs.getString("description"),
                TemplateStatus.fromCode(rs.getString("status")),
                rs.getInt("current_version"),
                (Integer) rs.getObject("published_version"),
                rs.getString("created_by"),
                rs.getString("reviewed_by"),
                instant(rs, "reviewed_at"),
                rs.getString("review_reason"),
                instant(rs, "updated_at"));
    }

    private static Instant instant(ResultSet rs, String column) throws SQLException {
        var value = rs.getTimestamp(column);
        return value == null ? null : value.toInstant();
    }
}
