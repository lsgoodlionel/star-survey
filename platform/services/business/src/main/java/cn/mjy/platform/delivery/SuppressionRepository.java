package cn.mjy.platform.delivery;

import cn.mjy.platform.shared.TenantId;
import java.util.UUID;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.stereotype.Repository;

/**
 * 退订名单。必须在 {@code TenantScope} 内调用。
 *
 * <p>只追加、不可撤销（运行期账号对该表没有 UPDATE/DELETE）：一条退订记录一旦落库，
 * 之后任何任务（首邀、催答、通知）在发送前都会查到它。这就是"退订不会被后续任务反转"的实现依据——
 * 不是靠应用层记得去查，而是靠"查得到且删不掉"。
 */
@Repository
class SuppressionRepository {

    private final JdbcClient jdbc;

    SuppressionRepository(JdbcClient jdbc) {
        this.jdbc = jdbc;
    }

    boolean isSuppressed(DeliveryChannel channel, String address) {
        return jdbc.sql("""
                        SELECT EXISTS (SELECT 1 FROM delivery_unsubscribe
                                        WHERE channel = :channel AND address = :address)
                        """)
                .param("channel", channel.code())
                .param("address", address)
                .query(Boolean.class)
                .single();
    }

    /** 登记退订；已在名单上时返回 false（幂等，不改原有的时刻与原因）。 */
    boolean add(TenantId tenant, DeliveryChannel channel, String address, String reason, UUID sourceTaskId) {
        return jdbc.sql("""
                        INSERT INTO delivery_unsubscribe (tenant_id, channel, address, reason, source_task_id)
                        VALUES (:tenant, :channel, :address, :reason, :task)
                        ON CONFLICT (tenant_id, channel, address) DO NOTHING
                        """)
                .param("tenant", tenant.value())
                .param("channel", channel.code())
                .param("address", address)
                .param("reason", reason)
                .param("task", sourceTaskId)
                .update() == 1;
    }
}
