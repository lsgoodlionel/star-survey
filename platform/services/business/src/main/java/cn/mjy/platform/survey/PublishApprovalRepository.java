package cn.mjy.platform.survey;

import cn.mjy.platform.shared.TenantId;
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

/**
 * survey_publish_approval 与 survey_publish_approval_event 的读写。必须在 {@code TenantScope} 内调用：
 * 查询不带 tenant_id 条件，可见范围完全由行级安全决定。改状态的方法只能在持有问卷行锁时调用，
 * 且一律带"从哪个状态迁出"的条件，返回是否真的迁移了。
 */
@Repository
class PublishApprovalRepository {

    /** 一次列出待审申请的上限；逐条按权限过滤，因此必须有界。 */
    static final int PENDING_LIMIT = 200;

    private static final String COLUMNS = """
            id, survey_id, draft_version, status, applicant, submitted_at, decided_by, decided_at, reason""";

    private final JdbcClient jdbc;

    PublishApprovalRepository(JdbcClient jdbc) {
        this.jdbc = jdbc;
    }

    void insertPending(TenantId tenant, UUID id, UUID surveyId, int draftVersion, String applicant) {
        jdbc.sql("""
                        INSERT INTO survey_publish_approval (tenant_id, id, survey_id, draft_version, status, applicant)
                        VALUES (:tenant, :id, :survey, :version, 'pending', :applicant)
                        """)
                .param("tenant", tenant.value())
                .param("id", id)
                .param("survey", surveyId)
                .param("version", draftVersion)
                .param("applicant", applicant)
                .update();
    }

    Optional<PublishApprovalView> find(UUID id) {
        return jdbc.sql("SELECT " + COLUMNS + " FROM survey_publish_approval WHERE id = :id")
                .param("id", id)
                .query(PublishApprovalRepository::map)
                .optional();
    }

    /** 该问卷的未结申请（pending 或 approved），加行锁。 */
    Optional<PublishApprovalView> lockOpen(UUID surveyId) {
        return jdbc.sql("SELECT " + COLUMNS + """
                         FROM survey_publish_approval
                        WHERE survey_id = :survey AND status IN ('pending', 'approved') FOR UPDATE
                        """)
                .param("survey", surveyId)
                .query(PublishApprovalRepository::map)
                .optional();
    }

    List<PublishApprovalView> forSurvey(UUID surveyId) {
        return jdbc.sql("SELECT " + COLUMNS + " FROM survey_publish_approval WHERE survey_id = :survey"
                        + " ORDER BY submitted_at, id")
                .param("survey", surveyId)
                .query(PublishApprovalRepository::map)
                .list();
    }

    List<PublishApprovalView> pending() {
        return jdbc.sql("SELECT " + COLUMNS + " FROM survey_publish_approval WHERE status = 'pending'"
                        + " ORDER BY submitted_at, id LIMIT :limit")
                .param("limit", PENDING_LIMIT)
                .query(PublishApprovalRepository::map)
                .list();
    }

    /** 条件迁移：只有当前状态在 from 之内才改为 to；返回迁移后的行，未迁移为空。 */
    Optional<PublishApprovalView> transition(UUID id, List<PublishApprovalStatus> from, PublishApprovalStatus to,
            String actor, String reason) {
        return jdbc.sql("""
                        UPDATE survey_publish_approval
                           SET status = :to, decided_by = :actor, decided_at = now(), reason = :reason
                         WHERE id = :id AND status IN (:from)
                        RETURNING""" + " " + COLUMNS)
                .param("to", to.code())
                .param("actor", actor)
                .param("reason", reason, Types.VARCHAR)
                .param("id", id)
                .param("from", from.stream().map(PublishApprovalStatus::code).toList())
                .query(PublishApprovalRepository::map)
                .optional();
    }

    void recordPublishAttempt(UUID id, UUID requestId) {
        jdbc.sql("UPDATE survey_publish_approval SET last_publish_request_id = :request WHERE id = :id")
                .param("request", requestId)
                .param("id", id)
                .update();
    }

    void appendEvent(TenantId tenant, PublishApprovalView approval, String event, String actor, String reason,
            UUID publishRequestId) {
        jdbc.sql("""
                        INSERT INTO survey_publish_approval_event (tenant_id, approval_id, survey_id, event, actor,
                            draft_version, reason, publish_request_id)
                        VALUES (:tenant, :approval, :survey, :event, :actor, :version, :reason, :request)
                        """)
                .param("tenant", tenant.value())
                .param("approval", approval.id())
                .param("survey", approval.surveyId())
                .param("event", event)
                .param("actor", actor)
                .param("version", approval.draftVersion())
                .param("reason", reason, Types.VARCHAR)
                .param("request", publishRequestId, Types.OTHER)
                .update();
    }

    List<PublishApprovalEventView> history(UUID approvalId) {
        return jdbc.sql("""
                        SELECT event, actor, draft_version, reason, publish_request_id, occurred_at
                          FROM survey_publish_approval_event WHERE approval_id = :approval ORDER BY id
                        """)
                .param("approval", approvalId)
                .query((rs, row) -> new PublishApprovalEventView(
                        rs.getString("event"),
                        rs.getString("actor"),
                        rs.getInt("draft_version"),
                        rs.getString("reason"),
                        rs.getObject("publish_request_id", UUID.class),
                        instant(rs.getTimestamp("occurred_at"))))
                .list();
    }

    private static PublishApprovalView map(ResultSet rs, int row) throws SQLException {
        return new PublishApprovalView(
                rs.getObject("id", UUID.class),
                rs.getObject("survey_id", UUID.class),
                rs.getInt("draft_version"),
                PublishApprovalStatus.fromCode(rs.getString("status")),
                rs.getString("applicant"),
                instant(rs.getTimestamp("submitted_at")),
                rs.getString("decided_by"),
                instant(rs.getTimestamp("decided_at")),
                rs.getString("reason"));
    }

    private static Instant instant(Timestamp timestamp) {
        return timestamp == null ? null : timestamp.toInstant();
    }
}
