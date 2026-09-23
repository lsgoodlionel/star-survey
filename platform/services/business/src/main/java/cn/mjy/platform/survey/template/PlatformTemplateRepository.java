package cn.mjy.platform.survey.template;

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
 * 平台（运营）模板两张表（V541）的读写。这是控制平面：表上没有行级安全，
 * 因为一份运营模板要对所有租户可见。可见性由查询条件承担——
 * 租户侧只能经 {@link #published()} 与 {@link #publishedDefinition} 读到已上架的版本。
 */
@Repository
class PlatformTemplateRepository {

    private static final String COLUMNS =
            "id, name, description, status, current_version, published_version, created_by, updated_at";

    private final JdbcClient jdbc;

    PlatformTemplateRepository(JdbcClient jdbc) {
        this.jdbc = jdbc;
    }

    void insert(UUID id, String name, String description, String createdBy) {
        jdbc.sql("""
                        INSERT INTO platform_survey_template
                            (id, name, description, status, current_version, created_by)
                        VALUES (:id, :name, :description, 'draft', 1, :createdBy)
                        """)
                .param("id", id)
                .param("name", name)
                .param("description", description, Types.VARCHAR)
                .param("createdBy", createdBy)
                .update();
    }

    Optional<TemplateView> find(UUID id) {
        return jdbc.sql("SELECT " + COLUMNS + " FROM platform_survey_template WHERE id = :id")
                .param("id", id)
                .query(PlatformTemplateRepository::map)
                .optional();
    }

    Optional<TemplateView> lock(UUID id) {
        return jdbc.sql("SELECT " + COLUMNS + " FROM platform_survey_template WHERE id = :id FOR UPDATE")
                .param("id", id)
                .query(PlatformTemplateRepository::map)
                .optional();
    }

    List<TemplateView> all() {
        return jdbc.sql("SELECT " + COLUMNS + " FROM platform_survey_template ORDER BY updated_at DESC, id")
                .query(PlatformTemplateRepository::map)
                .list();
    }

    /** 租户侧看到的运营模板库：只有已上架的。 */
    List<TemplateView> published() {
        return jdbc.sql("SELECT " + COLUMNS
                        + " FROM platform_survey_template WHERE status = 'published'"
                        + " ORDER BY updated_at DESC, id")
                .query(PlatformTemplateRepository::map)
                .list();
    }

    Optional<TemplateView> publish(UUID id, int versionNo) {
        return jdbc.sql("""
                        UPDATE platform_survey_template
                           SET status = 'published', published_version = :version, updated_at = now()
                         WHERE id = :id AND status IN ('draft', 'published', 'unpublished')
                           AND :version <= current_version
                        RETURNING """ + " " + COLUMNS)
                .param("id", id)
                .param("version", versionNo)
                .query(PlatformTemplateRepository::map)
                .optional();
    }

    Optional<TemplateView> unpublish(UUID id) {
        return jdbc.sql("""
                        UPDATE platform_survey_template
                           SET status = 'unpublished', published_version = NULL, updated_at = now()
                         WHERE id = :id AND status = 'published'
                        RETURNING """ + " " + COLUMNS)
                .param("id", id)
                .query(PlatformTemplateRepository::map)
                .optional();
    }

    int nextVersion(UUID id) {
        return jdbc.sql("""
                        UPDATE platform_survey_template
                           SET current_version = current_version + 1, updated_at = now()
                         WHERE id = :id
                        RETURNING current_version
                        """)
                .param("id", id)
                .query(Integer.class)
                .single();
    }

    void insertVersion(UUID templateId, int versionNo, String definition, String createdBy) {
        jdbc.sql("""
                        INSERT INTO platform_survey_template_version
                            (template_id, version_no, definition, created_by)
                        VALUES (:template, :version, CAST(:definition AS jsonb), :createdBy)
                        """)
                .param("template", templateId)
                .param("version", versionNo)
                .param("definition", definition)
                .param("createdBy", createdBy)
                .update();
    }

    /** 只返回**已上架**那一版的定义：租户拿不到草稿版或下架后的版本。 */
    Optional<String> publishedDefinition(UUID templateId, int versionNo) {
        return jdbc.sql("""
                        SELECT v.definition::text FROM platform_survey_template_version v
                          JOIN platform_survey_template t ON t.id = v.template_id
                         WHERE v.template_id = :template AND v.version_no = :version
                           AND t.status = 'published' AND t.published_version = v.version_no
                        """)
                .param("template", templateId)
                .param("version", versionNo)
                .query(String.class)
                .optional();
    }

    List<TemplateVersionView> versions(UUID templateId) {
        return jdbc.sql("""
                        SELECT version_no, created_by, created_at FROM platform_survey_template_version
                         WHERE template_id = :template ORDER BY version_no
                        """)
                .param("template", templateId)
                .query((rs, row) -> new TemplateVersionView(
                        rs.getInt("version_no"), null, null, rs.getString("created_by"),
                        rs.getTimestamp("created_at").toInstant()))
                .list();
    }

    private static TemplateView map(ResultSet rs, int row) throws SQLException {
        Instant updatedAt = rs.getTimestamp("updated_at") == null
                ? null : rs.getTimestamp("updated_at").toInstant();
        return new TemplateView(
                rs.getObject("id", UUID.class),
                TemplateScope.PLATFORM,
                rs.getString("name"),
                rs.getString("description"),
                TemplateStatus.fromCode(rs.getString("status")),
                rs.getInt("current_version"),
                (Integer) rs.getObject("published_version"),
                rs.getString("created_by"),
                null,
                null,
                null,
                updatedAt);
    }
}
