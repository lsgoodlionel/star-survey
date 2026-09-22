package cn.mjy.platform.survey;

import cn.mjy.platform.shared.TenantId;
import java.sql.ResultSet;
import java.sql.SQLException;
import java.time.OffsetDateTime;
import java.util.ArrayList;
import java.util.List;
import java.util.Optional;
import java.util.UUID;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.stereotype.Repository;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.json.JsonMapper;

/** survey_publish_attempt 表的读写。必须在 {@code TenantScope} 内、且持有问卷行锁时调用。 */
@Repository
class PublishAttemptRepository {

    static final String IN_FLIGHT = "in_flight";
    static final String PUBLISHED = "published";
    static final String FAILED = "failed";
    static final String UNKNOWN = "unknown";

    private static final String COLUMNS = """
            request_id, survey_id, draft_version, engine_instance_id, definition::text AS definition, outcome,
            gateway_status, failed_stage, failures::text AS failures, orphan_engine_sid, tries, next_reconcile_at,
            manual_review_at""";

    private final JdbcClient jdbc;
    private final JsonMapper json;

    PublishAttemptRepository(JdbcClient jdbc, JsonMapper json) {
        this.jdbc = jdbc;
        this.json = json;
    }

    record AttemptRow(UUID requestId, UUID surveyId, int draftVersion, String engineInstanceId, String definition,
            String outcome, Integer gatewayStatus, String failedStage, List<String> failures,
            Integer orphanEngineSid, int tries, OffsetDateTime nextReconcileAt, OffsetDateTime manualReviewAt) {

        PublishAttemptView view() {
            return new PublishAttemptView(requestId, engineInstanceId, draftVersion, outcome, gatewayStatus,
                    failedStage, failures, orphanEngineSid, tries, nextReconcileAt, manualReviewAt);
        }
    }

    /** 调用网关之前落库：requestId 先持久化，任何结局都能据此核对。 */
    void insert(TenantId tenant, UUID requestId, UUID surveyId, int draftVersion, String engineInstanceId,
            String definition, String requestedBy) {
        jdbc.sql("""
                        INSERT INTO survey_publish_attempt (tenant_id, request_id, survey_id, draft_version,
                            engine_instance_id, definition, outcome, requested_by)
                        VALUES (:tenant, :request, :survey, :draftVersion, :instance, CAST(:definition AS jsonb),
                            'in_flight', :requestedBy)
                        """)
                .param("tenant", tenant.value())
                .param("request", requestId)
                .param("survey", surveyId)
                .param("draftVersion", draftVersion)
                .param("instance", engineInstanceId)
                .param("definition", definition)
                .param("requestedBy", requestedBy)
                .update();
    }

    Optional<AttemptRow> find(UUID requestId) {
        return jdbc.sql("SELECT " + COLUMNS + " FROM survey_publish_attempt WHERE request_id = :request")
                .param("request", requestId)
                .query(this::map)
                .optional();
    }

    /** 同一 requestId 再试一次（核对）：结局回到 in_flight，计数加一。 */
    void markRetry(UUID requestId) {
        jdbc.sql("""
                        UPDATE survey_publish_attempt SET outcome = 'in_flight', tries = tries + 1, completed_at = NULL
                         WHERE request_id = :request
                        """)
                .param("request", requestId)
                .update();
    }

    void complete(UUID requestId, String outcome, Integer gatewayStatus, String failedStage, List<String> failures,
            Integer orphanEngineSid) {
        jdbc.sql("""
                        UPDATE survey_publish_attempt
                           SET outcome = :outcome, gateway_status = :status, failed_stage = :stage,
                               failures = CAST(:failures AS jsonb), orphan_engine_sid = :orphan, completed_at = now()
                         WHERE request_id = :request
                        """)
                .param("outcome", outcome)
                .param("status", gatewayStatus, java.sql.Types.INTEGER)
                .param("stage", failedStage, java.sql.Types.VARCHAR)
                .param("failures", json.writeValueAsString(failures))
                .param("orphan", orphanEngineSid, java.sql.Types.INTEGER)
                .param("request", requestId)
                .update();
    }

    private AttemptRow map(ResultSet rs, int row) throws SQLException {
        return new AttemptRow(
                rs.getObject("request_id", UUID.class),
                rs.getObject("survey_id", UUID.class),
                rs.getInt("draft_version"),
                rs.getString("engine_instance_id"),
                rs.getString("definition"),
                rs.getString("outcome"),
                (Integer) rs.getObject("gateway_status"),
                rs.getString("failed_stage"),
                texts(rs.getString("failures")),
                (Integer) rs.getObject("orphan_engine_sid"),
                rs.getInt("tries"),
                rs.getObject("next_reconcile_at", OffsetDateTime.class),
                rs.getObject("manual_review_at", OffsetDateTime.class));
    }

    private List<String> texts(String array) {
        List<String> values = new ArrayList<>();
        for (JsonNode item : json.readTree(array)) {
            values.add(item.asString());
        }
        return values;
    }
}
