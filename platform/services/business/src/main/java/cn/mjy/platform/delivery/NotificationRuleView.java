package cn.mjy.platform.delivery;

import cn.mjy.platform.delivery.NotificationRuleRepository.RuleRow;
import java.time.OffsetDateTime;
import java.util.UUID;

/** 新答卷通知规则的对外视图；地址脱敏。 */
public record NotificationRuleView(
        UUID id,
        UUID surveyId,
        String actorId,
        String channel,
        String address,
        long minIntervalSeconds,
        long notifiedCompletedCount,
        OffsetDateTime lastNotifiedAt,
        boolean enabled,
        OffsetDateTime createdAt) {

    static NotificationRuleView of(RuleRow row) {
        return new NotificationRuleView(row.id(), row.surveyId(), row.actorId(), row.channel().code(),
                Redaction.address(row.address()), row.minIntervalSeconds(), row.notifiedCompletedCount(),
                row.lastNotifiedAt(), row.enabled(), row.createdAt());
    }
}
