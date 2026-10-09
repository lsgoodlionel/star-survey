package cn.mjy.platform.survey.preview;

import cn.mjy.platform.shared.TenantId;
import java.sql.ResultSet;
import java.sql.SQLException;
import java.time.OffsetDateTime;
import java.util.List;
import java.util.Optional;
import java.util.UUID;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.stereotype.Repository;

@Repository
class PreviewSessionRepository {

    static final int CLOSE_LEASE_SECONDS = 150;

    private static final String COLUMNS = """
            id, request_id, survey_id, draft_version, requested_by, engine_instance_id, engine_sid,
            generation, preview_url, expires_at, status, failure, cleanup_attempts,
            created_at, updated_at, closed_at""";

    private final JdbcClient jdbc;

    PreviewSessionRepository(JdbcClient jdbc) {
        this.jdbc = jdbc;
    }

    record DraftSnapshot(int version, String definition) {
    }

    Optional<DraftSnapshot> draft(UUID surveyId) {
        return jdbc.sql("SELECT draft_version, draft_definition::text FROM survey WHERE id = :id")
                .param("id", surveyId)
                .query((rs, row) -> new DraftSnapshot(rs.getInt(1), rs.getString(2)))
                .optional();
    }

    String definition(UUID id) {
        return jdbc.sql("SELECT definition::text FROM survey_preview_session WHERE id = :id")
                .param("id", id).query(String.class).single();
    }

    boolean insert(TenantId tenant, UUID id, UUID requestId, UUID surveyId, DraftSnapshot draft,
            String actor, String instance, String generation, OffsetDateTime expiresAt) {
        return jdbc.sql("""
                INSERT INTO survey_preview_session
                    (tenant_id, id, request_id, survey_id, draft_version, definition, requested_by,
                     engine_instance_id, generation, expires_at, status)
                VALUES (:tenant, :id, :request, :survey, :version, CAST(:definition AS jsonb), :actor,
                        :instance, :generation, :expires, 'creating')
                ON CONFLICT (tenant_id, request_id) DO NOTHING
                """).param("tenant", tenant.value()).param("id", id).param("request", requestId)
                .param("survey", surveyId).param("version", draft.version()).param("definition", draft.definition())
                .param("actor", actor).param("instance", instance).param("generation", generation)
                .param("expires", expiresAt).update() == 1;
    }

    Optional<PreviewSessionView> find(UUID id) {
        return jdbc.sql("SELECT " + COLUMNS + " FROM survey_preview_session WHERE id = :id")
                .param("id", id).query(PreviewSessionRepository::map).optional();
    }

    Optional<PreviewSessionView> findByRequest(UUID requestId) {
        return jdbc.sql("SELECT " + COLUMNS + " FROM survey_preview_session WHERE request_id = :request")
                .param("request", requestId).query(PreviewSessionRepository::map).optional();
    }

    void ready(UUID id, int sid, String url) {
        jdbc.sql("""
                UPDATE survey_preview_session
                   SET status = 'ready', engine_sid = :sid, preview_url = :url,
                       failure = NULL, updated_at = now()
                 WHERE id = :id AND status = 'creating'
                """).param("id", id).param("sid", sid).param("url", url).update();
    }

    boolean bindSid(UUID id, int sid) {
        return jdbc.sql("""
                UPDATE survey_preview_session SET engine_sid = :sid, failure = NULL, updated_at = now()
                 WHERE id = :id AND status = 'creating' AND (engine_sid IS NULL OR engine_sid = :sid)
                """).param("id", id).param("sid", sid).update() == 1;
    }

    void failed(UUID id, String failure) {
        jdbc.sql("""
                UPDATE survey_preview_session SET status = 'failed', failure = :failure, updated_at = now()
                 WHERE id = :id AND status = 'creating'
                """).param("id", id).param("failure", failure).update();
    }

    Optional<UUID> claimClosing(UUID id) {
        UUID owner = UUID.randomUUID();
        int updated = jdbc.sql("""
                UPDATE survey_preview_session
                   SET status = 'closing', close_owner = :owner,
                       close_lease_until = now() + make_interval(secs => :leaseSeconds),
                       cleanup_attempts = cleanup_attempts + 1, updated_at = now()
                 WHERE id = :id AND (status IN ('ready', 'cleanup_failed')
                       OR (status = 'closing' AND close_lease_until <= now()))
                """).param("id", id).param("owner", owner)
                .param("leaseSeconds", CLOSE_LEASE_SECONDS).update();
        return updated == 1 ? Optional.of(owner) : Optional.empty();
    }

    boolean claimCreatingForCleanup(UUID id, UUID owner) {
        return jdbc.sql("""
                UPDATE survey_preview_session
                   SET status = 'closing', close_owner = :owner,
                       close_lease_until = now() + make_interval(secs => :leaseSeconds),
                       cleanup_attempts = cleanup_attempts + 1, updated_at = now()
                 WHERE id = :id AND status = 'creating' AND engine_sid IS NOT NULL
                """).param("id", id).param("owner", owner)
                .param("leaseSeconds", CLOSE_LEASE_SECONDS).update() == 1;
    }

    void closed(UUID id, UUID owner) {
        jdbc.sql("""
                UPDATE survey_preview_session
                   SET status = 'closed', failure = NULL, close_owner = NULL, close_lease_until = NULL,
                       closed_at = now(), updated_at = now()
                 WHERE id = :id AND status = 'closing' AND close_owner = :owner
                """).param("id", id).param("owner", owner).update();
    }

    void cleanupFailed(UUID id, UUID owner, String failure) {
        jdbc.sql("""
                UPDATE survey_preview_session
                   SET status = 'cleanup_failed', failure = :failure, close_owner = NULL,
                       close_lease_until = NULL, updated_at = now()
                 WHERE id = :id AND status = 'closing' AND close_owner = :owner
                """).param("id", id).param("owner", owner).param("failure", failure).update();
    }

    List<UUID> staleCreating(int limit) {
        return jdbc.sql("""
                SELECT id FROM survey_preview_session
                 WHERE status = 'creating' AND updated_at <= now() - interval '30 seconds'
                 ORDER BY updated_at LIMIT :limit
                """).param("limit", limit).query(UUID.class).list();
    }

    List<UUID> staleClosing(int limit) {
        return jdbc.sql("""
                SELECT id FROM survey_preview_session
                 WHERE status = 'closing' AND close_lease_until <= now()
                 ORDER BY close_lease_until LIMIT :limit
                """).param("limit", limit).query(UUID.class).list();
    }

    List<UUID> expired(int limit) {
        return jdbc.sql("""
                SELECT id FROM survey_preview_session
                 WHERE expires_at <= now() AND status IN ('ready', 'cleanup_failed')
                 ORDER BY expires_at LIMIT :limit
                """).param("limit", limit).query(UUID.class).list();
    }

    private static PreviewSessionView map(ResultSet rs, int row) throws SQLException {
        return new PreviewSessionView(rs.getObject("id", UUID.class), rs.getObject("request_id", UUID.class),
                rs.getObject("survey_id", UUID.class), rs.getInt("draft_version"), rs.getString("requested_by"),
                rs.getString("engine_instance_id"), (Integer) rs.getObject("engine_sid"),
                rs.getString("generation"), rs.getString("preview_url"),
                rs.getObject("expires_at", OffsetDateTime.class), rs.getString("status"), rs.getString("failure"),
                rs.getInt("cleanup_attempts"), rs.getObject("created_at", OffsetDateTime.class),
                rs.getObject("updated_at", OffsetDateTime.class), rs.getObject("closed_at", OffsetDateTime.class));
    }
}
