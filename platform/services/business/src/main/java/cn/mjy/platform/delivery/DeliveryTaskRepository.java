package cn.mjy.platform.delivery;

import cn.mjy.platform.shared.TenantId;
import java.sql.ResultSet;
import java.sql.SQLException;
import java.time.Duration;
import java.time.OffsetDateTime;
import java.util.List;
import java.util.Optional;
import java.util.UUID;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.stereotype.Repository;

/** delivery_task 的读写。必须在 {@code TenantScope} 内调用，可见范围由行级安全决定。 */
@Repository
class DeliveryTaskRepository {

    private static final String COLUMNS = """
            id, survey_id, link_id, channel, kind, subject, body_template, status, source_task_id,
            idempotency_key, queued_count, sent_count, failed_count, skipped_count, billed_units,
            attempts, next_attempt_at, last_error, created_by, created_at, started_at, finished_at""";

    /** 一个任务的存储形态。 */
    record TaskRow(
            UUID id,
            UUID surveyId,
            UUID linkId,
            DeliveryChannel channel,
            TaskKind kind,
            String subject,
            String bodyTemplate,
            TaskStatus status,
            UUID sourceTaskId,
            String idempotencyKey,
            int queuedCount,
            int sentCount,
            int failedCount,
            int skippedCount,
            int billedUnits,
            int attempts,
            OffsetDateTime nextAttemptAt,
            String lastError,
            String createdBy,
            OffsetDateTime createdAt,
            OffsetDateTime startedAt,
            OffsetDateTime finishedAt) {
    }

    private final JdbcClient jdbc;

    DeliveryTaskRepository(JdbcClient jdbc) {
        this.jdbc = jdbc;
    }

    void insert(TenantId tenant, UUID id, UUID surveyId, UUID linkId, DeliveryChannel channel, TaskKind kind,
            String subject, String bodyTemplate, UUID sourceTaskId, String idempotencyKey, String createdBy) {
        jdbc.sql("""
                        INSERT INTO delivery_task (tenant_id, id, survey_id, link_id, channel, kind, subject,
                                                   body_template, source_task_id, idempotency_key, created_by)
                        VALUES (:tenant, :id, :survey, :link, :channel, :kind, :subject,
                                :body, :source, :idempotency, :createdBy)
                        """)
                .param("tenant", tenant.value())
                .param("id", id)
                .param("survey", surveyId)
                .param("link", linkId)
                .param("channel", channel.code())
                .param("kind", kind.code())
                .param("subject", subject)
                .param("body", bodyTemplate)
                .param("source", sourceTaskId)
                .param("idempotency", idempotencyKey)
                .param("createdBy", createdBy)
                .update();
    }

    Optional<TaskRow> find(UUID id) {
        return jdbc.sql("SELECT " + COLUMNS + " FROM delivery_task WHERE id = :id")
                .param("id", id)
                .query(DeliveryTaskRepository::map)
                .optional();
    }

    Optional<TaskRow> findByIdempotencyKey(String createdBy, String key) {
        return jdbc.sql("SELECT " + COLUMNS + " FROM delivery_task"
                        + " WHERE created_by = :createdBy AND idempotency_key = :key")
                .param("createdBy", createdBy)
                .param("key", key)
                .query(DeliveryTaskRepository::map)
                .optional();
    }

    /** 本租户里到期可推进的任务，按登记顺序；只扫在途任务（部分索引）。 */
    List<UUID> dueTasks(int limit) {
        return jdbc.sql("""
                        SELECT id FROM delivery_task
                         WHERE status IN ('queued', 'running') AND next_attempt_at <= now()
                         ORDER BY created_at, id LIMIT :limit
                        """)
                .param("limit", limit)
                .query((rs, n) -> rs.getObject("id", UUID.class))
                .list();
    }

    /** 认领：只有仍在途的任务才会被置为 running，并原子地记一次尝试。 */
    boolean markRunning(UUID id) {
        return jdbc.sql("""
                        UPDATE delivery_task
                           SET status = 'running', attempts = attempts + 1,
                               started_at = COALESCE(started_at, now())
                         WHERE id = :id AND status IN ('queued', 'running')
                        """)
                .param("id", id)
                .update() == 1;
    }

    void finish(UUID id, TaskStatus status, String lastError) {
        jdbc.sql("""
                        UPDATE delivery_task
                           SET status = :status, finished_at = now(), last_error = :error
                         WHERE id = :id AND status IN ('queued', 'running')
                        """)
                .param("id", id)
                .param("status", status.code())
                .param("error", lastError)
                .update();
    }

    /** 本轮没做完（限速或预算用尽）：退回 queued 并推迟下一轮。 */
    void reschedule(UUID id, Duration delay) {
        jdbc.sql("""
                        UPDATE delivery_task
                           SET status = 'queued', next_attempt_at = now() + CAST(:delay AS interval)
                         WHERE id = :id AND status IN ('queued', 'running')
                        """)
                .param("id", id)
                .param("delay", delay.toSeconds() + " seconds")
                .update();
    }

    boolean cancel(UUID id) {
        return jdbc.sql("""
                        UPDATE delivery_task SET status = 'cancelled', finished_at = now()
                         WHERE id = :id AND status IN ('queued', 'running')
                        """)
                .param("id", id)
                .update() == 1;
    }

    /** 按收件人的最终状态累加计数；与收件人状态变更在同一事务里。 */
    void countOutcome(UUID id, RecipientState state) {
        String column = switch (state) {
            case SENT -> "sent_count";
            case FAILED, BOUNCED -> "failed_count";
            case SKIPPED, UNSUBSCRIBED -> "skipped_count";
            default -> throw new IllegalArgumentException("not a terminal recipient state: " + state);
        };
        jdbc.sql("UPDATE delivery_task SET " + column + " = " + column + " + 1 WHERE id = :id")
                .param("id", id)
                .update();
    }

    void setQueuedCount(UUID id, int queued) {
        jdbc.sql("UPDATE delivery_task SET queued_count = :queued WHERE id = :id")
                .param("id", id)
                .param("queued", queued)
                .update();
    }

    /** 计费：只在回执首次落库时调用一次。 */
    void addBilledUnits(UUID id, int units) {
        jdbc.sql("UPDATE delivery_task SET billed_units = billed_units + :units WHERE id = :id")
                .param("id", id)
                .param("units", units)
                .update();
    }

    private static TaskRow map(ResultSet rs, int rowNum) throws SQLException {
        return new TaskRow(
                rs.getObject("id", UUID.class),
                rs.getObject("survey_id", UUID.class),
                rs.getObject("link_id", UUID.class),
                DeliveryChannel.require(rs.getString("channel")),
                TaskKind.fromCode(rs.getString("kind")),
                rs.getString("subject"),
                rs.getString("body_template"),
                TaskStatus.fromCode(rs.getString("status")),
                rs.getObject("source_task_id", UUID.class),
                rs.getString("idempotency_key"),
                rs.getInt("queued_count"),
                rs.getInt("sent_count"),
                rs.getInt("failed_count"),
                rs.getInt("skipped_count"),
                rs.getInt("billed_units"),
                rs.getInt("attempts"),
                rs.getObject("next_attempt_at", OffsetDateTime.class),
                rs.getString("last_error"),
                rs.getString("created_by"),
                rs.getObject("created_at", OffsetDateTime.class),
                rs.getObject("started_at", OffsetDateTime.class),
                rs.getObject("finished_at", OffsetDateTime.class));
    }
}
