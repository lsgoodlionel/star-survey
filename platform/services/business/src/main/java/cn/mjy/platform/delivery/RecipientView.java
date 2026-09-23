package cn.mjy.platform.delivery;

import cn.mjy.platform.delivery.RecipientRepository.RecipientRow;
import java.time.OffsetDateTime;
import java.util.UUID;

/**
 * 单个收件人的投递状态。
 *
 * <p>{@code address} 是脱敏后的形式（{@link Redaction}）：进度页要能看出"发给谁了"，
 * 但没必要把整份通讯录再抄一遍到接口响应里。需要原始名单的人去看联系人模块。
 */
public record RecipientView(
        UUID id,
        String address,
        String displayName,
        String state,
        int attempts,
        int remindersSent,
        String lastError,
        OffsetDateTime sentAt,
        OffsetDateTime lastReminderAt) {

    static RecipientView of(RecipientRow row) {
        return new RecipientView(row.id(), Redaction.address(row.address()), row.displayName(),
                row.state().code(), row.attempts(), row.remindersSent(), row.lastError(), row.sentAt(),
                row.lastReminderAt());
    }
}
