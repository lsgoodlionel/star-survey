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

/** delivery_notification_rule 的读写。必须在 {@code TenantScope} 内调用。 */
@Repository
class NotificationRuleRepository {

    private static final String COLUMNS = """
            id, survey_id, actor_id, channel, address, min_interval_seconds,
            notified_completed_count, last_notified_at, enabled, created_by, created_at""";

    /** 一条新答卷通知规则。 */
    record RuleRow(
            UUID id,
            UUID surveyId,
            String actorId,
            DeliveryChannel channel,
            String address,
            int minIntervalSeconds,
            long notifiedCompletedCount,
            OffsetDateTime lastNotifiedAt,
            boolean enabled,
            String createdBy,
            OffsetDateTime createdAt) {
    }

    private final JdbcClient jdbc;

    NotificationRuleRepository(JdbcClient jdbc) {
        this.jdbc = jdbc;
    }

    /** 同一 (问卷, 成员, 渠道) 已有规则时什么都不做并返回 false。 */
    boolean insert(TenantId tenant, UUID id, UUID surveyId, String actorId, DeliveryChannel channel,
            String address, int minIntervalSeconds, String createdBy) {
        return jdbc.sql("""
                        INSERT INTO delivery_notification_rule (tenant_id, id, survey_id, actor_id, channel,
                            address, min_interval_seconds, created_by)
                        VALUES (:tenant, :id, :survey, :actor, :channel, :address, :interval, :createdBy)
                        ON CONFLICT (tenant_id, survey_id, actor_id, channel) DO NOTHING
                        """)
                .param("tenant", tenant.value())
                .param("id", id)
                .param("survey", surveyId)
                .param("actor", actorId)
                .param("channel", channel.code())
                .param("address", address)
                .param("interval", minIntervalSeconds)
                .param("createdBy", createdBy)
                .update() == 1;
    }

    Optional<RuleRow> find(UUID id) {
        return jdbc.sql("SELECT " + COLUMNS + " FROM delivery_notification_rule WHERE id = :id")
                .param("id", id)
                .query(NotificationRuleRepository::map)
                .optional();
    }

    List<RuleRow> listBySurvey(UUID surveyId) {
        return jdbc.sql("SELECT " + COLUMNS + " FROM delivery_notification_rule"
                        + " WHERE survey_id = :survey ORDER BY created_at")
                .param("survey", surveyId)
                .query(NotificationRuleRepository::map)
                .list();
    }

    /** 到期可以再通知一次的规则（还没通知过，或距上次已超过最小间隔）。 */
    List<UUID> due() {
        return jdbc.sql("""
                        SELECT id FROM delivery_notification_rule
                         WHERE enabled
                           AND (last_notified_at IS NULL
                                OR last_notified_at + CAST(min_interval_seconds || ' seconds' AS interval) <= now())
                         ORDER BY created_at
                        """)
                .query((rs, n) -> rs.getObject("id", UUID.class))
                .list();
    }

    /** 取规则并加锁：两个副本不会为同一条规则各发一次通知。 */
    Optional<RuleRow> lock(UUID id) {
        return jdbc.sql("SELECT " + COLUMNS + " FROM delivery_notification_rule"
                        + " WHERE id = :id FOR UPDATE SKIP LOCKED")
                .param("id", id)
                .query(NotificationRuleRepository::map)
                .optional();
    }

    void markNotified(UUID id, long completedCount) {
        jdbc.sql("""
                        UPDATE delivery_notification_rule
                           SET notified_completed_count = :count, last_notified_at = now()
                         WHERE id = :id
                        """)
                .param("id", id)
                .param("count", completedCount)
                .update();
    }

    boolean setEnabled(UUID id, boolean enabled) {
        return jdbc.sql("UPDATE delivery_notification_rule SET enabled = :enabled WHERE id = :id")
                .param("id", id)
                .param("enabled", enabled)
                .update() == 1;
    }

    private static RuleRow map(ResultSet rs, int rowNum) throws SQLException {
        return new RuleRow(
                rs.getObject("id", UUID.class),
                rs.getObject("survey_id", UUID.class),
                rs.getString("actor_id"),
                DeliveryChannel.require(rs.getString("channel")),
                rs.getString("address"),
                rs.getInt("min_interval_seconds"),
                rs.getLong("notified_completed_count"),
                rs.getObject("last_notified_at", OffsetDateTime.class),
                rs.getBoolean("enabled"),
                rs.getString("created_by"),
                rs.getObject("created_at", OffsetDateTime.class));
    }
}
