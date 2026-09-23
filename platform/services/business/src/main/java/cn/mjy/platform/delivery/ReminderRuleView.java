package cn.mjy.platform.delivery;

import cn.mjy.platform.delivery.ReminderRuleRepository.RuleRow;
import java.time.OffsetDateTime;
import java.util.UUID;

/** 催答规则的对外视图。 */
public record ReminderRuleView(
        UUID id,
        UUID surveyId,
        UUID sourceTaskId,
        long firstDelaySeconds,
        long intervalSeconds,
        int maxReminders,
        String subject,
        boolean enabled,
        OffsetDateTime createdAt) {

    static ReminderRuleView of(RuleRow row) {
        return new ReminderRuleView(row.id(), row.surveyId(), row.sourceTaskId(), row.firstDelaySeconds(),
                row.intervalSeconds(), row.maxReminders(), row.subject(), row.enabled(), row.createdAt());
    }
}
