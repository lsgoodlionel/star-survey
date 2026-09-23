package cn.mjy.platform.delivery;

import cn.mjy.platform.shared.TenantId;
import java.sql.ResultSet;
import java.sql.SQLException;
import java.time.OffsetDateTime;
import java.util.List;
import java.util.Optional;
import java.util.UUID;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.stereotype.Repository;

/** delivery_reminder_rule 的读写。必须在 {@code TenantScope} 内调用。 */
@Repository
class ReminderRuleRepository {

    private static final String COLUMNS = """
            id, survey_id, source_task_id, first_delay_seconds, interval_seconds, max_reminders,
            subject, body_template, enabled, created_by, created_at""";

    /** 一条催答规则。 */
    record RuleRow(
            UUID id,
            UUID surveyId,
            UUID sourceTaskId,
            int firstDelaySeconds,
            int intervalSeconds,
            int maxReminders,
            String subject,
            String bodyTemplate,
            boolean enabled,
            String createdBy,
            OffsetDateTime createdAt) {
    }

    private final JdbcClient jdbc;

    ReminderRuleRepository(JdbcClient jdbc) {
        this.jdbc = jdbc;
    }

    void insert(TenantId tenant, UUID id, UUID surveyId, UUID sourceTaskId, int firstDelaySeconds,
            int intervalSeconds, int maxReminders, String subject, String bodyTemplate, String createdBy) {
        jdbc.sql("""
                        INSERT INTO delivery_reminder_rule (tenant_id, id, survey_id, source_task_id,
                            first_delay_seconds, interval_seconds, max_reminders, subject, body_template, created_by)
                        VALUES (:tenant, :id, :survey, :source, :firstDelay, :interval, :max,
                                :subject, :body, :createdBy)
                        """)
                .param("tenant", tenant.value())
                .param("id", id)
                .param("survey", surveyId)
                .param("source", sourceTaskId)
                .param("firstDelay", firstDelaySeconds)
                .param("interval", intervalSeconds)
                .param("max", maxReminders)
                .param("subject", subject)
                .param("body", bodyTemplate)
                .param("createdBy", createdBy)
                .update();
    }

    Optional<RuleRow> find(UUID id) {
        return jdbc.sql("SELECT " + COLUMNS + " FROM delivery_reminder_rule WHERE id = :id")
                .param("id", id)
                .query(ReminderRuleRepository::map)
                .optional();
    }

    Optional<RuleRow> findBySourceTask(UUID sourceTaskId) {
        return jdbc.sql("SELECT " + COLUMNS + " FROM delivery_reminder_rule WHERE source_task_id = :source")
                .param("source", sourceTaskId)
                .query(ReminderRuleRepository::map)
                .optional();
    }

    List<RuleRow> listBySurvey(UUID surveyId) {
        return jdbc.sql("SELECT " + COLUMNS + " FROM delivery_reminder_rule"
                        + " WHERE survey_id = :survey ORDER BY created_at")
                .param("survey", surveyId)
                .query(ReminderRuleRepository::map)
                .list();
    }

    List<RuleRow> enabled() {
        return jdbc.sql("SELECT " + COLUMNS + " FROM delivery_reminder_rule WHERE enabled ORDER BY created_at")
                .query(ReminderRuleRepository::map)
                .list();
    }

    List<UUID> surveysWithEnabledRules() {
        return jdbc.sql("SELECT DISTINCT survey_id FROM delivery_reminder_rule WHERE enabled ORDER BY survey_id")
                .query((rs, n) -> rs.getObject("survey_id", UUID.class))
                .list();
    }

    boolean setEnabled(UUID id, boolean enabled) {
        return jdbc.sql("UPDATE delivery_reminder_rule SET enabled = :enabled WHERE id = :id")
                .param("id", id)
                .param("enabled", enabled)
                .update() == 1;
    }

    private static RuleRow map(ResultSet rs, int rowNum) throws SQLException {
        return new RuleRow(
                rs.getObject("id", UUID.class),
                rs.getObject("survey_id", UUID.class),
                rs.getObject("source_task_id", UUID.class),
                rs.getInt("first_delay_seconds"),
                rs.getInt("interval_seconds"),
                rs.getInt("max_reminders"),
                rs.getString("subject"),
                rs.getString("body_template"),
                rs.getBoolean("enabled"),
                rs.getString("created_by"),
                rs.getObject("created_at", OffsetDateTime.class));
    }
}
