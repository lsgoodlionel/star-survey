package cn.mjy.platform.contacts;

import java.sql.ResultSet;
import java.sql.SQLException;
import java.time.OffsetDateTime;
import java.util.List;
import java.util.Optional;
import java.util.UUID;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.stereotype.Repository;

/** 联系人 ↔ 参与者令牌映射的读写。须在 {@code TenantScope} 内调用。 */
@Repository
class ContactParticipationRepository {

    private static final String COLUMNS = """
            id, contact_id, survey_id, version_no, engine_instance_id, engine_sid, participant_token, issued_at""";

    private final JdbcClient jdbc;

    ContactParticipationRepository(JdbcClient jdbc) {
        this.jdbc = jdbc;
    }

    ParticipationView insert(UUID id, UUID contactId, UUID surveyId, int versionNo, String instanceId,
            int engineSid, String token, String issuedBy) {
        return jdbc.sql("""
                        INSERT INTO contact_participation (tenant_id, id, contact_id, survey_id, version_no,
                            engine_instance_id, engine_sid, participant_token, issued_by)
                        VALUES (app_current_tenant(), :id, :contact, :survey, :version, :instance, :sid, :token, :by)
                        RETURNING """ + " " + COLUMNS)
                .param("id", id).param("contact", contactId).param("survey", surveyId)
                .param("version", versionNo).param("instance", instanceId).param("sid", engineSid)
                .param("token", token).param("by", issuedBy)
                .query(ContactParticipationRepository::toView).single();
    }

    Optional<ParticipationView> findCurrent(UUID contactId, UUID surveyId, int versionNo) {
        return jdbc.sql("SELECT " + COLUMNS + " FROM contact_participation WHERE contact_id = :contact"
                        + " AND survey_id = :survey AND version_no = :version AND revoked_at IS NULL")
                .param("contact", contactId).param("survey", surveyId).param("version", versionNo)
                .query(ContactParticipationRepository::toView).optional();
    }

    Optional<ParticipationView> find(UUID id) {
        return jdbc.sql("SELECT " + COLUMNS + " FROM contact_participation WHERE id = :id AND revoked_at IS NULL")
                .param("id", id).query(ContactParticipationRepository::toView).optional();
    }

    List<ParticipationView> forSurvey(UUID surveyId, int versionNo, int limit) {
        return jdbc.sql("SELECT " + COLUMNS + " FROM contact_participation WHERE survey_id = :survey"
                        + " AND version_no = :version AND revoked_at IS NULL ORDER BY issued_at, id LIMIT :limit")
                .param("survey", surveyId).param("version", versionNo).param("limit", limit)
                .query(ContactParticipationRepository::toView).list();
    }

    boolean revoke(UUID id, String actorId) {
        return jdbc.sql("""
                UPDATE contact_participation SET revoked_at = now(), revoked_by = :by
                 WHERE id = :id AND revoked_at IS NULL
                """).param("id", id).param("by", actorId).update() == 1;
    }

    private static ParticipationView toView(ResultSet rs, int row) throws SQLException {
        return new ParticipationView(rs.getObject("id", UUID.class), rs.getObject("contact_id", UUID.class),
                rs.getObject("survey_id", UUID.class), rs.getInt("version_no"),
                rs.getString("engine_instance_id"), rs.getInt("engine_sid"),
                rs.getString("participant_token"), rs.getObject("issued_at", OffsetDateTime.class));
    }
}
