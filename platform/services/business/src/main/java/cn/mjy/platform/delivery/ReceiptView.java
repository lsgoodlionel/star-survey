package cn.mjy.platform.delivery;

import java.time.OffsetDateTime;
import java.util.UUID;

/** 已认证、已去重的投递回执视图；不暴露服务商 eventId、消息内容或原始地址。 */
public record ReceiptView(
        UUID recipientId,
        String provider,
        String kind,
        boolean billable,
        OffsetDateTime occurredAt,
        OffsetDateTime receivedAt) {
}
