package cn.mjy.platform.contacts;

import java.sql.ResultSet;
import java.sql.SQLException;
import java.time.OffsetDateTime;
import java.util.Optional;
import java.util.UUID;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.stereotype.Repository;

/** 问卷受众条件的读写。须在 {@code TenantScope} 内调用。 */
@Repository
class SurveyAudienceRepository {

    private static final String COLUMNS =
            "survey_id, list_id, org_unit_id, include_descendants, tag, updated_by, updated_at";

    private final JdbcClient jdbc;

    SurveyAudienceRepository(JdbcClient jdbc) {
        this.jdbc = jdbc;
    }

    /** 一份问卷只有一条受众：再次设定就是整条替换。 */
    SurveyAudienceView upsert(UUID surveyId, UUID listId, UUID orgUnitId, boolean includeDescendants,
            String tag, String actorId) {
        return jdbc.sql("""
                        INSERT INTO survey_audience (tenant_id, survey_id, list_id, org_unit_id,
                            include_descendants, tag, updated_by)
                        VALUES (app_current_tenant(), :survey, :list, :unit, :descendants, :tag, :by)
                        ON CONFLICT (tenant_id, survey_id) DO UPDATE
                            SET list_id = EXCLUDED.list_id, org_unit_id = EXCLUDED.org_unit_id,
                                include_descendants = EXCLUDED.include_descendants, tag = EXCLUDED.tag,
                                updated_by = EXCLUDED.updated_by, updated_at = now()
                        RETURNING """ + " " + COLUMNS)
                .param("survey", surveyId).param("list", listId).param("unit", orgUnitId)
                .param("descendants", includeDescendants).param("tag", tag).param("by", actorId)
                .query(SurveyAudienceRepository::toView).single();
    }

    Optional<SurveyAudienceView> find(UUID surveyId) {
        return jdbc.sql("SELECT " + COLUMNS + " FROM survey_audience WHERE survey_id = :survey")
                .param("survey", surveyId).query(SurveyAudienceRepository::toView).optional();
    }

    boolean delete(UUID surveyId) {
        return jdbc.sql("DELETE FROM survey_audience WHERE survey_id = :survey")
                .param("survey", surveyId).update() == 1;
    }

    private static SurveyAudienceView toView(ResultSet rs, int row) throws SQLException {
        return new SurveyAudienceView(rs.getObject("survey_id", UUID.class),
                rs.getObject("list_id", UUID.class), rs.getObject("org_unit_id", UUID.class),
                rs.getBoolean("include_descendants"), rs.getString("tag"), rs.getString("updated_by"),
                rs.getObject("updated_at", OffsetDateTime.class));
    }
}
