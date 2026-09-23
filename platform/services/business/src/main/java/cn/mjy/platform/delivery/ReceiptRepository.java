package cn.mjy.platform.delivery;

import cn.mjy.platform.shared.TenantId;
import java.time.OffsetDateTime;
import java.util.Optional;
import java.util.UUID;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.stereotype.Repository;

/**
 * delivery_receipt 的读写。必须在 {@code TenantScope} 内调用。
 *
 * <p>去重完全交给数据库的唯一键 {@code (tenant_id, provider, event_id)}：
 * 插入用 {@code ON CONFLICT DO NOTHING}，只有真正插进去的那一次才会触发计费与状态变更。
 * 并发重复投递同一条回执时，唯一键让其中一个插入返回 0 行，因此不会重复计费。
 */
@Repository
class ReceiptRepository {

    /** 回执匹配到的发送记录。 */
    record SendMatch(UUID taskId, UUID recipientId) {
    }

    private final JdbcClient jdbc;

    ReceiptRepository(JdbcClient jdbc) {
        this.jdbc = jdbc;
    }

    /** 按平台自己生成的幂等键匹配：这是最可靠的对应关系，渠道商只需把它原样带回来。 */
    Optional<SendMatch> findByIdempotencyKey(String idempotencyKey) {
        return jdbc.sql("""
                        SELECT task_id, recipient_id FROM delivery_send WHERE idempotency_key = :key
                        """)
                .param("key", idempotencyKey)
                .query((rs, n) -> new SendMatch(rs.getObject("task_id", UUID.class),
                        rs.getObject("recipient_id", UUID.class)))
                .optional();
    }

    /** 退而求其次：按渠道商自己的消息号匹配（部分服务商不回传自定义字段）。 */
    Optional<SendMatch> findByProviderMessageId(String providerMessageId) {
        return jdbc.sql("""
                        SELECT task_id, recipient_id FROM delivery_send
                         WHERE provider_message_id = :id LIMIT 1
                        """)
                .param("id", providerMessageId)
                .query((rs, n) -> new SendMatch(rs.getObject("task_id", UUID.class),
                        rs.getObject("recipient_id", UUID.class)))
                .optional();
    }

    /** 落库；同一 (渠道商, 事件号) 已存在时返回 false，调用方据此跳过计费与状态变更。 */
    boolean insert(TenantId tenant, UUID id, String provider, String eventId, String kind, SendMatch match,
            boolean billable, OffsetDateTime occurredAt) {
        return jdbc.sql("""
                        INSERT INTO delivery_receipt (tenant_id, id, provider, event_id, kind, task_id,
                                                      recipient_id, billable, occurred_at)
                        VALUES (:tenant, :id, :provider, :event, :kind, :task, :recipient, :billable, :occurredAt)
                        ON CONFLICT (tenant_id, provider, event_id) DO NOTHING
                        """)
                .param("tenant", tenant.value())
                .param("id", id)
                .param("provider", provider)
                .param("event", eventId)
                .param("kind", kind)
                .param("task", match.taskId())
                .param("recipient", match.recipientId())
                .param("billable", billable)
                .param("occurredAt", occurredAt)
                .update() == 1;
    }
}
