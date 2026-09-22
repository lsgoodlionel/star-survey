package cn.mjy.platform.response;

import cn.mjy.platform.shared.TenantId;
import java.sql.PreparedStatement;
import java.sql.ResultSet;
import java.sql.SQLException;
import java.sql.Types;
import java.time.Duration;
import java.time.Instant;
import java.time.OffsetDateTime;
import java.time.ZoneOffset;
import java.util.List;
import java.util.Optional;
import java.util.UUID;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.stereotype.Repository;

/**
 * 导出作业与快照行的读写。全部在 {@code TenantScope} 内调用（行级安全）；租约相关的写入都带
 * {@code lease_token = :token AND status = 'running'} 条件，返回是否仍由调用方持有（ADR 0015 决定 1）。
 */
@Repository
class ResponseExportRepository {

    private static final String OPEN = "status IN ('queued', 'running')";
    private static final String FREE = "(lease_until IS NULL OR lease_until < now())";
    private static final String MINE = "id = :id AND lease_token = :token AND status = 'running'";
    private static final String LEASE_UNTIL = "now() + make_interval(secs => :leaseSeconds)";

    /** 新作业需要的字段。 */
    record NewJob(TenantId tenant, UUID id, UUID surveyId, String requestedBy, ExportFormat format,
            String templateVersion, String filterJson, String planJson, boolean revealSensitive,
            String idempotencyKey, int batchSize, Duration retention) {
    }

    private final JdbcClient jdbc;
    private final JdbcTemplate batch;

    ResponseExportRepository(JdbcClient jdbc, JdbcTemplate batch) {
        this.jdbc = jdbc;
        this.batch = batch;
    }

    ExportJob insert(NewJob job) {
        return jdbc.sql("""
                        INSERT INTO response_export_job (tenant_id, id, survey_id, requested_by, format,
                            template_version, filter_snapshot, plan, reveal_sensitive, idempotency_key, batch_size, expires_at)
                        VALUES (:tenant, :id, :survey, :by, :format, :template, CAST(:filter AS jsonb),
                            CAST(:plan AS jsonb), :reveal, :key, :batch, now() + make_interval(secs => :retention))
                        RETURNING *, filter_snapshot::text AS filter_text, plan::text AS plan_text
                        """)
                .param("tenant", job.tenant().value()).param("id", job.id()).param("survey", job.surveyId())
                .param("by", job.requestedBy()).param("format", job.format().code())
                .param("template", job.templateVersion()).param("filter", job.filterJson())
                .param("plan", job.planJson()).param("reveal", job.revealSensitive())
                .param("key", job.idempotencyKey(), Types.VARCHAR).param("batch", job.batchSize())
                .param("retention", (double) job.retention().toSeconds())
                .query(ResponseExportRepository::mapJob).single();
    }

    Optional<ExportJob> find(UUID id) {
        return jdbc.sql(select("id = :id")).param("id", id).query(ResponseExportRepository::mapJob).optional();
    }

    Optional<ExportJob> findOwned(UUID id, String requestedBy) {
        return jdbc.sql(select("id = :id AND requested_by = :by")).param("id", id).param("by", requestedBy)
                .query(ResponseExportRepository::mapJob).optional();
    }

    Optional<ExportJob> findByIdempotencyKey(String requestedBy, String key) {
        return jdbc.sql(select("requested_by = :by AND idempotency_key = :key"))
                .param("by", requestedBy).param("key", key)
                .query(ResponseExportRepository::mapJob).optional();
    }

    /** 可以认领的作业：未结束、退避已到、没有有效租约。 */
    List<UUID> claimable(int limit) {
        return jdbc.sql("SELECT id FROM response_export_job WHERE " + OPEN + " AND next_attempt_at <= now() AND "
                        + FREE + " ORDER BY created_at, id LIMIT :limit")
                .param("limit", limit).query(UUID.class).list();
    }

    Optional<ExportJob> claim(UUID id, UUID token, Duration lease) {
        return jdbc.sql("UPDATE response_export_job SET status = 'running', started_at = COALESCE(started_at, now()),"
                        + " lease_token = :token, lease_until = " + LEASE_UNTIL
                        + " WHERE id = :id AND " + OPEN + " AND next_attempt_at <= now() AND " + FREE
                        + " RETURNING *, filter_snapshot::text AS filter_text, plan::text AS plan_text")
                .param("id", id).param("token", token).param("leaseSeconds", seconds(lease))
                .query(ResponseExportRepository::mapJob).optional();
    }

    boolean snapshotProgress(UUID id, UUID token, int version, String generation, Long responseId,
            Duration lease) {
        return jdbc.sql("UPDATE response_export_job SET snapshot_version = :version, snapshot_generation = :gen,"
                        + " snapshot_response_id = :rid, lease_until = " + LEASE_UNTIL + " WHERE " + MINE)
                .param("version", version).param("gen", generation, Types.VARCHAR)
                .param("rid", responseId, Types.BIGINT).param("leaseSeconds", seconds(lease))
                .param("id", id).param("token", token).update() == 1;
    }

    boolean snapshotDone(UUID id, UUID token, long totalRows, Duration lease) {
        return jdbc.sql("UPDATE response_export_job SET snapshot_done = true, total_rows = :total,"
                        + " lease_until = " + LEASE_UNTIL + " WHERE " + MINE)
                .param("total", totalRows).param("leaseSeconds", seconds(lease))
                .param("id", id).param("token", token).update() == 1;
    }

    long maxSeq(UUID jobId) {
        return jdbc.sql("SELECT COALESCE(MAX(seq), 0) FROM response_export_item WHERE job_id = :job")
                .param("job", jobId).query(Long.class).single();
    }

    void insertItems(TenantId tenant, UUID jobId, List<ExportItem> items) {
        batch.batchUpdate("""
                        INSERT INTO response_export_item (tenant_id, job_id, seq, version_no, engine_instance_id,
                            engine_sid, generation, response_id, state, first_event_at, completed_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """, items, items.size(),
                (PreparedStatement ps, ExportItem item) -> {
                    ps.setObject(1, tenant.value());
                    ps.setObject(2, jobId);
                    ps.setLong(3, item.seq());
                    ps.setInt(4, item.version());
                    ps.setString(5, item.engineInstanceId());
                    ps.setLong(6, item.engineSid());
                    ps.setString(7, item.generation());
                    ps.setLong(8, item.responseId());
                    ps.setString(9, item.state());
                    ps.setObject(10, utc(item.firstEventAt()));
                    ps.setObject(11, utc(item.completedAt()));
                });
    }

    List<ExportItem> items(UUID jobId, long fromSeq, int limit) {
        return jdbc.sql("""
                        SELECT seq, version_no, engine_instance_id, engine_sid, generation, response_id, state,
                               first_event_at, completed_at
                          FROM response_export_item WHERE job_id = :job AND seq >= :from
                         ORDER BY seq LIMIT :limit
                        """)
                .param("job", jobId).param("from", fromSeq).param("limit", limit)
                .query((rs, n) -> new ExportItem(rs.getLong("seq"), rs.getInt("version_no"),
                        rs.getString("engine_instance_id"), rs.getLong("engine_sid"), rs.getString("generation"),
                        rs.getLong("response_id"), rs.getString("state"), instant(rs, "first_event_at"),
                        instant(rs, "completed_at")))
                .list();
    }

    boolean advance(UUID id, UUID token, long nextSeq, Duration lease) {
        return jdbc.sql("UPDATE response_export_job SET next_seq = :next, lease_until = " + LEASE_UNTIL
                        + " WHERE " + MINE + " AND next_seq < :next")
                .param("next", nextSeq).param("leaseSeconds", seconds(lease))
                .param("id", id).param("token", token).update() == 1;
    }

    boolean complete(UUID id, UUID token, String fileKey, long size, String sha256) {
        return jdbc.sql("""
                        UPDATE response_export_job
                           SET status = 'completed', file_key = :key, file_size = :size, file_sha256 = :sha,
                               finished_at = now(), lease_token = NULL, lease_until = NULL, last_error = NULL
                         WHERE id = :id AND lease_token = :token AND status = 'running'
                        """)
                .param("key", fileKey).param("size", size).param("sha", sha256)
                .param("id", id).param("token", token).update() == 1;
    }

    boolean fail(UUID id, UUID token, String error) {
        return jdbc.sql("UPDATE response_export_job SET status = 'failed', last_error = :error, finished_at = now(),"
                        + " lease_token = NULL, lease_until = NULL WHERE " + MINE)
                .param("error", error).param("id", id).param("token", token).update() == 1;
    }

    /** 记一次失败并按退避重排；累计达到上限时直接转 failed。返回是否已转 failed。 */
    Optional<Boolean> retryLater(UUID id, UUID token, String error, Duration backoff, int maxAttempts) {
        return jdbc.sql("""
                        UPDATE response_export_job
                           SET attempts = attempts + 1, last_error = :error,
                               next_attempt_at = now() + make_interval(secs => :backoff),
                               status = CASE WHEN attempts + 1 >= :max THEN 'failed' ELSE status END,
                               finished_at = CASE WHEN attempts + 1 >= :max THEN now() ELSE finished_at END,
                               lease_token = NULL, lease_until = NULL
                         WHERE id = :id AND lease_token = :token AND status = 'running'
                        RETURNING status = 'failed'
                        """)
                .param("error", error).param("backoff", seconds(backoff)).param("max", maxAttempts)
                .param("id", id).param("token", token).query(Boolean.class).optional();
    }

    void release(UUID id, UUID token) {
        jdbc.sql("UPDATE response_export_job SET lease_token = NULL, lease_until = NULL WHERE " + MINE)
                .param("id", id).param("token", token).update();
    }

    boolean cancel(UUID id, String requestedBy) {
        return jdbc.sql("UPDATE response_export_job SET status = 'cancelled', finished_at = now(),"
                        + " lease_token = NULL, lease_until = NULL"
                        + " WHERE id = :id AND requested_by = :by AND " + OPEN)
                .param("id", id).param("by", requestedBy).update() == 1;
    }

    void deleteItems(UUID jobId) {
        jdbc.sql("DELETE FROM response_export_item WHERE job_id = :job").param("job", jobId).update();
    }

    List<UUID> expired(int limit) {
        return jdbc.sql("SELECT id FROM response_export_job WHERE status <> 'expired' AND expires_at <= now()"
                        + " ORDER BY expires_at LIMIT :limit")
                .param("limit", limit).query(UUID.class).list();
    }

    boolean markExpired(UUID id) {
        return jdbc.sql("UPDATE response_export_job SET status = 'expired', lease_token = NULL, lease_until = NULL,"
                        + " finished_at = COALESCE(finished_at, now())"
                        + " WHERE id = :id AND status <> 'expired' AND expires_at <= now()")
                .param("id", id).update() == 1;
    }

    private static String select(String where) {
        return "SELECT *, filter_snapshot::text AS filter_text, plan::text AS plan_text FROM response_export_job"
                + " WHERE " + where;
    }

    private static double seconds(Duration duration) {
        return duration.toMillis() / 1000.0;
    }

    private static ExportJob mapJob(ResultSet rs, int rowNum) throws SQLException {
        return new ExportJob(rs.getObject("id", UUID.class), rs.getObject("survey_id", UUID.class),
                rs.getString("requested_by"),
                ExportFormat.fromCode(rs.getString("format")).orElseThrow(),
                rs.getString("template_version"), rs.getString("filter_text"), rs.getString("plan_text"),
                rs.getBoolean("reveal_sensitive"), rs.getString("idempotency_key"), rs.getInt("batch_size"),
                ExportStatus.fromDb(rs.getString("status")), rs.getBoolean("snapshot_done"),
                rs.getObject("snapshot_version", Integer.class), rs.getString("snapshot_generation"),
                rs.getObject("snapshot_response_id", Long.class), rs.getObject("total_rows", Long.class),
                rs.getLong("next_seq"), rs.getInt("attempts"), rs.getObject("lease_token", UUID.class),
                rs.getString("last_error"), rs.getString("file_key"), rs.getObject("file_size", Long.class),
                rs.getString("file_sha256"), instant(rs, "created_at"), instant(rs, "started_at"),
                instant(rs, "finished_at"), instant(rs, "expires_at"));
    }

    private static OffsetDateTime utc(Instant instant) {
        return instant == null ? null : instant.atOffset(ZoneOffset.UTC);
    }

    private static Instant instant(ResultSet rs, String column) throws SQLException {
        OffsetDateTime value = rs.getObject(column, OffsetDateTime.class);
        return value == null ? null : value.toInstant();
    }
}
