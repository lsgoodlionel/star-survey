package cn.mjy.platform.survey;

import cn.mjy.platform.shared.TenantId;
import java.sql.ResultSet;
import java.sql.SQLException;
import java.time.Duration;
import java.util.Optional;
import java.util.UUID;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.stereotype.Repository;

/**
 * survey 表的读写。必须在 {@code TenantScope} 内调用：查询不带 tenant_id 条件，可见范围完全由行级安全决定。
 * 状态迁移只在持有 {@link #lock} 取得的行锁时进行。
 */
@Repository
class SurveyRepository {

    private static final String COLUMNS = """
            id, title, status, draft_version, draft_definition::text AS draft_definition,
            current_request_id, published_version,
            (SELECT v.draft_version FROM survey_published_version v
              WHERE v.survey_id = survey.id AND v.version_no = survey.published_version) AS live_draft_version""";

    private final JdbcClient jdbc;

    SurveyRepository(JdbcClient jdbc) {
        this.jdbc = jdbc;
    }

    /**
     * 一行问卷。stale 仅在 {@link #lock} 时计算：publishing 已超过给定时长仍未收尾。
     * publishedVersion 是当前在线（被公开路由指向）的版本，liveDraftVersion 是它发布自哪个草稿版本；
     * 从未发布过时两者都为空。
     */
    record SurveyRow(UUID id, String title, SurveyStatus status, int draftVersion, String draftDefinition,
            UUID currentRequestId, Integer publishedVersion, Integer liveDraftVersion, boolean stale) {

        /** 已有在线版本，且草稿就是它发布时的那一版：没有可以重新发布的改动。 */
        boolean liveVersionIsCurrentDraft() {
            return liveDraftVersion != null && liveDraftVersion == draftVersion;
        }
    }

    void insert(TenantId tenant, UUID id, String title, String definition, String createdBy) {
        jdbc.sql("""
                        INSERT INTO survey (tenant_id, id, title, status, draft_definition, draft_version, created_by)
                        VALUES (:tenant, :id, :title, 'draft', CAST(:definition AS jsonb), 1, :createdBy)
                        """)
                .param("tenant", tenant.value())
                .param("id", id)
                .param("title", title)
                .param("definition", definition)
                .param("createdBy", createdBy)
                .update();
    }

    Optional<SurveyRow> find(UUID id) {
        return jdbc.sql("SELECT " + COLUMNS + ", false AS stale FROM survey WHERE id = :id")
                .param("id", id)
                .query(SurveyRepository::map)
                .optional();
    }

    /** 取行锁（SELECT … FOR UPDATE）：同一问卷的并发发布在这里串行化。 */
    Optional<SurveyRow> lock(UUID id, Duration staleAfter) {
        return jdbc.sql("SELECT " + COLUMNS + """
                        , (status = 'publishing' AND publishing_started_at < now() - make_interval(secs => :stale))
                          AS stale
                        FROM survey WHERE id = :id FOR UPDATE
                        """)
                .param("id", id)
                .param("stale", (double) staleAfter.toSeconds())
                .query(SurveyRepository::map)
                .optional();
    }

    /** 乐观锁保存草稿：只有当前版本等于 expectedVersion 时才写入；返回新版本，版本不符返回空。 */
    Optional<Integer> updateDraft(UUID id, int expectedVersion, String title, String definition) {
        return jdbc.sql("""
                        UPDATE survey
                           SET draft_definition = CAST(:definition AS jsonb), title = :title,
                               draft_version = draft_version + 1, updated_at = now()
                         WHERE id = :id AND draft_version = :expected
                        RETURNING draft_version
                        """)
                .param("definition", definition)
                .param("title", title)
                .param("id", id)
                .param("expected", expectedVersion)
                .query(Integer.class)
                .optional();
    }

    void markPublishing(UUID id, UUID requestId) {
        jdbc.sql("""
                        UPDATE survey SET status = 'publishing', current_request_id = :request,
                               publishing_started_at = now(), updated_at = now()
                         WHERE id = :id
                        """)
                .param("request", requestId)
                .param("id", id)
                .update();
    }

    void markSettled(UUID id, SurveyStatus status, Integer publishedVersion) {
        if (status == SurveyStatus.PUBLISHING || status == SurveyStatus.DRAFT) {
            throw new IllegalArgumentException("not a settled status: " + status);
        }
        jdbc.sql("""
                        UPDATE survey SET status = :status, published_version = COALESCE(CAST(:version AS integer), published_version),
                               publishing_started_at = NULL, updated_at = now()
                         WHERE id = :id
                        """)
                .param("status", status.code())
                .param("version", publishedVersion)
                .param("id", id)
                .update();
    }

    private static SurveyRow map(ResultSet rs, int row) throws SQLException {
        return new SurveyRow(
                rs.getObject("id", UUID.class),
                rs.getString("title"),
                SurveyStatus.fromCode(rs.getString("status")),
                rs.getInt("draft_version"),
                rs.getString("draft_definition"),
                rs.getObject("current_request_id", UUID.class),
                (Integer) rs.getObject("published_version"),
                (Integer) rs.getObject("live_draft_version"),
                rs.getBoolean("stale"));
    }
}
