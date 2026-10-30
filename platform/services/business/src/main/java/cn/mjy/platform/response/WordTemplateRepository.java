package cn.mjy.platform.response;

import cn.mjy.platform.shared.TenantId;
import java.sql.ResultSet;
import java.sql.SQLException;
import java.time.Instant;
import java.time.OffsetDateTime;
import java.util.List;
import java.util.Optional;
import java.util.UUID;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.stereotype.Repository;

@Repository
class WordTemplateRepository {

    record Metadata(UUID id, UUID surveyId, String name, int version, String sha256,
            long byteSize, Instant createdAt) {
    }

    record Version(UUID id, UUID surveyId, String name, int version, byte[] content, String sha256,
            long byteSize, Instant createdAt) {
    }

    private static final String LATEST = """
            SELECT t.id, t.survey_id, t.name, v.version_no, v.content, v.sha256, v.byte_size, v.created_at
              FROM response_word_template t
              JOIN response_word_template_version v
                ON v.tenant_id = t.tenant_id AND v.template_id = t.id AND v.version_no = t.current_version
            """;

    private static final String LATEST_METADATA = """
            SELECT t.id, t.survey_id, t.name, v.version_no, v.sha256, v.byte_size, v.created_at
              FROM response_word_template t
              JOIN response_word_template_version v
                ON v.tenant_id = t.tenant_id AND v.template_id = t.id AND v.version_no = t.current_version
            """;

    private final JdbcClient jdbc;

    WordTemplateRepository(JdbcClient jdbc) {
        this.jdbc = jdbc;
    }

    Version insert(TenantId tenant, UUID surveyId, UUID id, String name, byte[] content, String sha256,
            String actor) {
        jdbc.sql("""
                INSERT INTO response_word_template (tenant_id, survey_id, id, name, created_by)
                VALUES (:tenant, :survey, :id, :name, :actor)
                """).param("tenant", tenant.value()).param("survey", surveyId).param("id", id)
                .param("name", name).param("actor", actor).update();
        jdbc.sql("""
                INSERT INTO response_word_template_version
                    (tenant_id, template_id, version_no, content, byte_size, sha256, created_by)
                VALUES (:tenant, :id, 1, :content, :size, :sha, :actor)
                """).param("tenant", tenant.value()).param("id", id).param("content", content)
                .param("size", content.length).param("sha", sha256).param("actor", actor).update();
        return findVersion(id, 1).orElseThrow();
    }

    Optional<Version> addVersion(UUID surveyId, UUID id, byte[] content, String sha256, String actor) {
        return jdbc.sql("""
                WITH bumped AS (
                    UPDATE response_word_template
                       SET current_version = current_version + 1, updated_at = now()
                     WHERE id = :id AND survey_id = :survey
                 RETURNING tenant_id, id, current_version
                ), inserted AS (
                    INSERT INTO response_word_template_version
                        (tenant_id, template_id, version_no, content, byte_size, sha256, created_by)
                    SELECT tenant_id, id, current_version, :content, :size, :sha, :actor FROM bumped
                    RETURNING template_id, version_no
                )
                SELECT version_no FROM inserted
                """).param("id", id).param("survey", surveyId).param("content", content)
                .param("size", content.length).param("sha", sha256).param("actor", actor)
                .query(Integer.class).optional().flatMap(version -> findVersion(id, version));
    }

    void lockSurvey(UUID surveyId) {
        jdbc.sql("SELECT id FROM survey WHERE id = :survey FOR UPDATE")
                .param("survey", surveyId).query(UUID.class).single();
    }

    long count(UUID surveyId) {
        return jdbc.sql("SELECT count(*) FROM response_word_template WHERE survey_id = :survey")
                .param("survey", surveyId).query(Long.class).single();
    }

    Optional<Integer> lockCurrentVersion(UUID surveyId, UUID id) {
        return jdbc.sql("""
                SELECT current_version FROM response_word_template
                 WHERE survey_id = :survey AND id = :id FOR UPDATE
                """).param("survey", surveyId).param("id", id).query(Integer.class).optional();
    }

    List<Metadata> list(UUID surveyId, int limit, int offset) {
        return jdbc.sql(LATEST_METADATA
                        + " WHERE t.survey_id = :survey ORDER BY t.updated_at DESC, t.id LIMIT :limit OFFSET :offset")
                .param("survey", surveyId).param("limit", limit).param("offset", offset)
                .query(WordTemplateRepository::mapMetadata).list();
    }

    Optional<Version> find(UUID surveyId, UUID id) {
        return jdbc.sql(LATEST + " WHERE t.survey_id = :survey AND t.id = :id")
                .param("survey", surveyId).param("id", id).query(WordTemplateRepository::map).optional();
    }

    Optional<Version> findVersion(UUID surveyId, UUID id, int version) {
        return jdbc.sql("""
                SELECT t.id, t.survey_id, t.name, v.version_no, v.content, v.sha256, v.byte_size, v.created_at
                  FROM response_word_template t
                  JOIN response_word_template_version v
                    ON v.tenant_id = t.tenant_id AND v.template_id = t.id
                 WHERE t.survey_id = :survey AND t.id = :id AND v.version_no = :version
                """).param("survey", surveyId).param("id", id).param("version", version)
                .query(WordTemplateRepository::map).optional();
    }

    private Optional<Version> findVersion(UUID id, int version) {
        return jdbc.sql("""
                SELECT t.id, t.survey_id, t.name, v.version_no, v.content, v.sha256, v.byte_size, v.created_at
                  FROM response_word_template t
                  JOIN response_word_template_version v
                    ON v.tenant_id = t.tenant_id AND v.template_id = t.id
                 WHERE t.id = :id AND v.version_no = :version
                """).param("id", id).param("version", version).query(WordTemplateRepository::map).optional();
    }

    private static Version map(ResultSet rs, int row) throws SQLException {
        OffsetDateTime created = rs.getObject("created_at", OffsetDateTime.class);
        return new Version(rs.getObject("id", UUID.class), rs.getObject("survey_id", UUID.class),
                rs.getString("name"), rs.getInt("version_no"), rs.getBytes("content"), rs.getString("sha256"),
                rs.getLong("byte_size"), created.toInstant());
    }

    private static Metadata mapMetadata(ResultSet rs, int row) throws SQLException {
        OffsetDateTime created = rs.getObject("created_at", OffsetDateTime.class);
        return new Metadata(rs.getObject("id", UUID.class), rs.getObject("survey_id", UUID.class),
                rs.getString("name"), rs.getInt("version_no"), rs.getString("sha256"),
                rs.getLong("byte_size"), created.toInstant());
    }
}
