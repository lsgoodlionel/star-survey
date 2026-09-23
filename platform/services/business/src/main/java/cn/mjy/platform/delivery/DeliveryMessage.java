package cn.mjy.platform.delivery;

import java.util.Objects;

/**
 * 交给渠道商的一条消息。{@code address} 是租户联系数据，{@link #toString()} 已脱敏，
 * 因此即使整条消息被打进日志也不会泄漏收件人。
 *
 * @param idempotencyKey 按 (任务, 收件人) 生成、随发送台账固定不变；重发必须带同一个键
 */
public record DeliveryMessage(
        DeliveryChannel channel,
        String address,
        String subject,
        String body,
        String idempotencyKey) {

    public DeliveryMessage {
        Objects.requireNonNull(channel, "channel");
        Objects.requireNonNull(address, "address");
        Objects.requireNonNull(body, "body");
        Objects.requireNonNull(idempotencyKey, "idempotencyKey");
    }

    @Override
    public String toString() {
        return "DeliveryMessage[channel=" + channel.code() + ", address=" + Redaction.address(address)
                + ", idempotencyKey=" + idempotencyKey + "]";
    }
}
