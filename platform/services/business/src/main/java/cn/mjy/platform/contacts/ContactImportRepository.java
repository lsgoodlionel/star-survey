package cn.mjy.platform.contacts;

import java.sql.PreparedStatement;
import java.sql.ResultSet;
import java.sql.SQLException;
import java.sql.Types;
import java.time.Duration;
import java.util.List;
import java.util.Optional;
import java.util.UUID;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.stereotype.Repository;

/**
 * 导入作业、原始行与逐行结果的读写。须在 {@code TenantScope} 内调用。
 * 带租约的写入都附 {@code lease_token = :token AND status = 'running'}，返回是否仍由调用方持有。
 */
@Repository
class ContactImportRepository {

    private static final String OPEN = "status IN ('queued', 'running')";
    private static final String FREE = "(lease_until IS NULL OR lease_until < now())";
    private static final String MINE = "id = :id AND lease_token = :token AND status = 'running'";
    private static final String LEASE_UNTIL = "now() + make_interval(secs => :leaseSeconds)";

    private static final String COLUMNS = """
            id, list_id, format, requested_by, status, total_rows, next_row,
            created_count, updated_count, duplicate_count, invalid_count, attempts, lease_token""";

    /** 作业行（仅后台推进需要的字段）。 */
    record ImportJob(UUID id, UUID listId, String format, String requestedBy, String status, int totalRows,
            int nextRow, int created, int updated, int duplicate, int invalid, int attempts, UUID leaseToken) {

        ImportJobView toView() {
            return new ImportJobView(id, listId, status, totalRows, nextRow - 1, created, updated, duplicate,
                    invalid);
        }

        boolean isDone() {
            return nextRow > totalRows;
        }
    }

    private final JdbcClient jdbc;
    private final JdbcTemplate batch;

    ContactImportRepository(JdbcClient jdbc, JdbcTemplate batch) {
        this.jdbc = jdbc;
        this.batch = batch;
    }

    ImportJob insert(UUID id, UUID listId, String format, String requestedBy, String idempotencyKey,
            int totalRows) {
        return jdbc.sql("""
                        INSERT INTO contact_import_job (tenant_id, id, list_id, format, requested_by,
                            idempotency_key, total_rows)
                        VALUES (app_current_tenant(), :id, :list, :format, :by, :key, :total)
                        RETURNING """ + " " + COLUMNS)
                .param("id", id).param("list", listId).param("format", format).param("by", requestedBy)
                .param("key", idempotencyKey, Types.VARCHAR).param("total", totalRows)
                .query(ContactImportRepository::toJob).single();
    }

    /** 一行原始数据：行号从 1 开始，payload 是该行的 JSON 对象原文。 */
    record RowPayload(int rowNo, String json) {
    }

    void insertRows(UUID jobId, List<RowPayload> payloads) {
        batch.batchUpdate("""
                        INSERT INTO contact_import_row (tenant_id, job_id, row_no, payload)
                        VALUES (app_current_tenant(), ?, ?, CAST(? AS jsonb))
                        """, payloads, payloads.size(), (PreparedStatement ps, RowPayload payload) -> {
                    ps.setObject(1, jobId);
                    ps.setInt(2, payload.rowNo());
                    ps.setString(3, payload.json());
                });
    }

    Optional<ImportJob> find(UUID id) {
        return jdbc.sql("SELECT " + COLUMNS + " FROM contact_import_job WHERE id = :id")
                .param("id", id).query(ContactImportRepository::toJob).optional();
    }

    Optional<ImportJob> findByIdempotencyKey(String requestedBy, String key) {
        return jdbc.sql("SELECT " + COLUMNS + " FROM contact_import_job"
                        + " WHERE requested_by = :by AND idempotency_key = :key")
                .param("by", requestedBy).param("key", key)
                .query(ContactImportRepository::toJob).optional();
    }

    List<UUID> claimable(int limit) {
        return jdbc.sql("SELECT id FROM contact_import_job WHERE " + OPEN + " AND next_attempt_at <= now() AND "
                        + FREE + " ORDER BY created_at, id LIMIT :limit")
                .param("limit", limit).query(UUID.class).list();
    }

    Optional<ImportJob> claim(UUID id, UUID token, Duration lease) {
        return jdbc.sql("UPDATE contact_import_job SET status = 'running',"
                        + " started_at = COALESCE(started_at, now()), lease_token = :token,"
                        + " lease_until = " + LEASE_UNTIL
                        + " WHERE id = :id AND " + OPEN + " AND next_attempt_at <= now() AND " + FREE
                        + " RETURNING " + COLUMNS)
                .param("id", id).param("token", token).param("leaseSeconds", seconds(lease))
                .query(ContactImportRepository::toJob).optional();
    }

    Optional<String> payload(UUID jobId, int rowNo) {
        return jdbc.sql("SELECT payload::text FROM contact_import_row WHERE job_id = :job AND row_no = :row")
                .param("job", jobId).param("row", rowNo).query(String.class).optional();
    }

    /**
     * 记录这一行的结果并把断点推进一格。结果行已存在（崩溃后重放同一行）时保留原结果、不重复计数，
     * 保证"恢复后不重复建人"。返回是否仍持有租约。
     */
    boolean commitRow(UUID jobId, UUID token, int rowNo, String outcome, String reason, UUID contactId,
            Duration lease) {
        boolean recorded = jdbc.sql("""
                INSERT INTO contact_import_outcome (tenant_id, job_id, row_no, outcome, reason, contact_id)
                VALUES (app_current_tenant(), :job, :row, :outcome, :reason, :contact)
                ON CONFLICT (tenant_id, job_id, row_no) DO NOTHING
                """).param("job", jobId).param("row", rowNo).param("outcome", outcome)
                .param("reason", reason, Types.VARCHAR).param("contact", contactId)
                .update() == 1;
        String counter = switch (outcome) {
            case "created" -> "created_count";
            case "updated" -> "updated_count";
            case "duplicate" -> "duplicate_count";
            default -> "invalid_count";
        };
        String bump = recorded ? ", " + counter + " = " + counter + " + 1" : "";
        return jdbc.sql("UPDATE contact_import_job SET next_row = :row + 1" + bump
                        + ", lease_until = " + LEASE_UNTIL + " WHERE " + MINE + " AND next_row = :row")
                .param("row", rowNo).param("leaseSeconds", seconds(lease))
                .param("id", jobId).param("token", token).update() == 1;
    }

    boolean finish(UUID jobId, UUID token) {
        boolean done = jdbc.sql("UPDATE contact_import_job SET status = 'completed', finished_at = now(),"
                        + " lease_token = NULL, lease_until = NULL WHERE " + MINE)
                .param("id", jobId).param("token", token).update() == 1;
        if (done) {
            jdbc.sql("DELETE FROM contact_import_row WHERE job_id = :job").param("job", jobId).update();
        }
        return done;
    }

    /** 终结为失败（不再重试），只记错误码。 */
    boolean fail(UUID jobId, UUID token, String error) {
        return jdbc.sql("UPDATE contact_import_job SET status = 'failed', finished_at = now(),"
                        + " last_error = :error, lease_token = NULL, lease_until = NULL WHERE " + MINE)
                .param("id", jobId).param("token", token).param("error", error).update() == 1;
    }

    /** 这一行命中的联系人是否已经被本次作业写过（文件内重复的判据）。 */
    boolean touchedByJob(UUID jobId, UUID contactId) {
        return jdbc.sql("SELECT count(*) FROM contact_import_outcome WHERE job_id = :job"
                        + " AND contact_id = :contact")
                .param("job", jobId).param("contact", contactId).query(Long.class).single() > 0;
    }

    void release(UUID jobId, UUID token) {
        jdbc.sql("UPDATE contact_import_job SET lease_token = NULL, lease_until = NULL WHERE " + MINE)
                .param("id", jobId).param("token", token).update();
    }

    /** 失败后退避重试；重试次数用尽则终结为 failed。返回"是否已终结"。 */
    Optional<Boolean> retryLater(UUID jobId, UUID token, String error, Duration backoff, int maxAttempts) {
        return jdbc.sql("""
                        UPDATE contact_import_job SET attempts = attempts + 1, last_error = :error,
                            next_attempt_at = now() + make_interval(secs => :backoff),
                            status = CASE WHEN attempts + 1 >= :max THEN 'failed' ELSE status END,
                            finished_at = CASE WHEN attempts + 1 >= :max THEN now() ELSE finished_at END,
                            lease_token = NULL, lease_until = NULL
                         WHERE """ + MINE + " RETURNING status = 'failed'")
                .param("id", jobId).param("token", token).param("error", error)
                .param("backoff", seconds(backoff)).param("max", maxAttempts)
                .query(Boolean.class).optional();
    }

    List<ImportRowOutcome> outcomes(UUID jobId, int fromRow, int limit) {
        return jdbc.sql("""
                        SELECT row_no, outcome, reason, contact_id FROM contact_import_outcome
                         WHERE job_id = :job AND row_no >= :from ORDER BY row_no LIMIT :limit
                        """).param("job", jobId).param("from", fromRow).param("limit", limit)
                .query((rs, n) -> new ImportRowOutcome(rs.getInt("row_no"), rs.getString("outcome"),
                        rs.getString("reason"), rs.getObject("contact_id", UUID.class)))
                .list();
    }

    private static double seconds(Duration duration) {
        return duration.toMillis() / 1000.0;
    }

    private static ImportJob toJob(ResultSet rs, int row) throws SQLException {
        return new ImportJob(rs.getObject("id", UUID.class), rs.getObject("list_id", UUID.class),
                rs.getString("format"), rs.getString("requested_by"), rs.getString("status"),
                rs.getInt("total_rows"), rs.getInt("next_row"), rs.getInt("created_count"),
                rs.getInt("updated_count"), rs.getInt("duplicate_count"), rs.getInt("invalid_count"),
                rs.getInt("attempts"), rs.getObject("lease_token", UUID.class));
    }
}
