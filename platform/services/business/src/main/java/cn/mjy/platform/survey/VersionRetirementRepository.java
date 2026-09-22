package cn.mjy.platform.survey;

import cn.mjy.platform.shared.TenantId;
import java.time.Duration;
import java.util.List;
import java.util.UUID;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.stereotype.Repository;

/**
 * survey_version_retirement 的读写（V530）：被新版本取代的已发布版本，以及它在引擎里的收口进度。
 * 必须在 {@code TenantScope} 内调用，可见范围由行级安全决定。行不删除。
 */
@Repository
class VersionRetirementRepository {

    private final JdbcClient jdbc;

    VersionRetirementRepository(JdbcClient jdbc) {
        this.jdbc = jdbc;
    }

    /** 尚未收口的一份旧版本。 */
    record OpenRetirement(UUID surveyId, int versionNo, String engineInstanceId, int engineSid, int closeAttempts) {
    }

    /** 路由切换的同一事务里调用：旧版本自此被取代，等待收口。重复写入（核对重放）什么都不做。 */
    void insert(TenantId tenant, UUID surveyId, int versionNo, int supersededBy, String instanceId, int sid) {
        jdbc.sql("""
                        INSERT INTO survey_version_retirement (tenant_id, survey_id, version_no, superseded_by_version,
                            engine_instance_id, engine_sid)
                        VALUES (:tenant, :survey, :version, :by, :instance, :sid)
                        ON CONFLICT DO NOTHING
                        """)
                .param("tenant", tenant.value())
                .param("survey", surveyId)
                .param("version", versionNo)
                .param("by", supersededBy)
                .param("instance", instanceId)
                .param("sid", sid)
                .update();
    }

    /** 某问卷尚未收口、且退避已到的旧版本（发布收尾后立即收口用）。 */
    List<OpenRetirement> openFor(UUID surveyId) {
        return jdbc.sql("""
                        SELECT survey_id, version_no, engine_instance_id, engine_sid, close_attempts
                          FROM survey_version_retirement
                         WHERE survey_id = :survey AND engine_closed_at IS NULL
                           AND (next_close_at IS NULL OR next_close_at <= now())
                         ORDER BY version_no
                        """)
                .param("survey", surveyId)
                .query(VersionRetirementRepository::map)
                .list();
    }

    /** 本租户内到期的未收口旧版本，最早被取代的在前（后台重试用）。 */
    List<OpenRetirement> due(int limit) {
        return jdbc.sql("""
                        SELECT survey_id, version_no, engine_instance_id, engine_sid, close_attempts
                          FROM survey_version_retirement
                         WHERE engine_closed_at IS NULL AND (next_close_at IS NULL OR next_close_at <= now())
                         ORDER BY superseded_at, survey_id, version_no
                         LIMIT :limit
                        """)
                .param("limit", limit)
                .query(VersionRetirementRepository::map)
                .list();
    }

    void markClosed(UUID surveyId, int versionNo, String engineExpires) {
        jdbc.sql("""
                        UPDATE survey_version_retirement
                           SET engine_closed_at = COALESCE(engine_closed_at, now()),
                               engine_expires = COALESCE(engine_expires, :expires),
                               close_attempts = close_attempts + 1, last_close_error = NULL, next_close_at = NULL
                         WHERE survey_id = :survey AND version_no = :version
                        """)
                .param("expires", engineExpires)
                .param("survey", surveyId)
                .param("version", versionNo)
                .update();
    }

    void markCloseFailed(UUID surveyId, int versionNo, String error, Duration retryAfter) {
        jdbc.sql("""
                        UPDATE survey_version_retirement
                           SET close_attempts = close_attempts + 1, last_close_error = :error,
                               next_close_at = now() + make_interval(secs => :delay)
                         WHERE survey_id = :survey AND version_no = :version AND engine_closed_at IS NULL
                        """)
                .param("error", error)
                .param("delay", (double) retryAfter.toSeconds())
                .param("survey", surveyId)
                .param("version", versionNo)
                .update();
    }

    private static OpenRetirement map(java.sql.ResultSet rs, int row) throws java.sql.SQLException {
        return new OpenRetirement(
                rs.getObject("survey_id", UUID.class),
                rs.getInt("version_no"),
                rs.getString("engine_instance_id"),
                rs.getInt("engine_sid"),
                rs.getInt("close_attempts"));
    }
}
