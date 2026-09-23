package cn.mjy.platform.delivery;

import cn.mjy.platform.engine.ResponseProjectionQuery.Position;
import cn.mjy.platform.shared.TenantId;
import java.util.Optional;
import java.util.UUID;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.stereotype.Repository;

/** 完成对账的游标（键集分页位置）。必须在 {@code TenantScope} 内调用。 */
@Repository
class CompletionCursorRepository {

    private final JdbcClient jdbc;

    CompletionCursorRepository(JdbcClient jdbc) {
        this.jdbc = jdbc;
    }

    Optional<Position> find(UUID surveyId) {
        return jdbc.sql("""
                        SELECT generation, response_id FROM delivery_completion_cursor WHERE survey_id = :survey
                        """)
                .param("survey", surveyId)
                .query((rs, n) -> new Position(rs.getString("generation"), rs.getLong("response_id")))
                .optional();
    }

    void save(TenantId tenant, UUID surveyId, String generation, long responseId) {
        jdbc.sql("""
                        INSERT INTO delivery_completion_cursor (tenant_id, survey_id, generation, response_id)
                        VALUES (:tenant, :survey, :generation, :response)
                        ON CONFLICT (tenant_id, survey_id)
                        DO UPDATE SET generation = :generation, response_id = :response, updated_at = now()
                        """)
                .param("tenant", tenant.value())
                .param("survey", surveyId)
                .param("generation", generation)
                .param("response", responseId)
                .update();
    }
}
