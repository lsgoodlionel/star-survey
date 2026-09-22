package cn.mjy.platform.identity.org;

import cn.mjy.platform.shared.TenantId;
import java.util.UUID;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.stereotype.Repository;

/** org_event_receipt：已处理事件的回执（去重）。必须在 {@code TenantScope} 内调用。 */
@Repository
class OrgEventReceiptRepository {

    private final JdbcClient jdbc;

    OrgEventReceiptRepository(JdbcClient jdbc) {
        this.jdbc = jdbc;
    }

    boolean exists(UUID connectionId, String eventKey) {
        return jdbc.sql("""
                        SELECT EXISTS (SELECT 1 FROM org_event_receipt
                                       WHERE connection_id = :connection AND event_key = :key)
                        """)
                .param("connection", connectionId)
                .param("key", eventKey)
                .query(Boolean.class)
                .single();
    }

    /** 记回执；并发重复投递时只有一条生效。返回是否为本次写入。 */
    boolean record(TenantId tenant, UUID connectionId, String eventKey, String eventType, int departed) {
        return jdbc.sql("""
                        INSERT INTO org_event_receipt (tenant_id, connection_id, event_key, event_type, departed)
                        VALUES (:tenant, :connection, :key, :type, :departed)
                        ON CONFLICT DO NOTHING
                        """)
                .param("tenant", tenant.value())
                .param("connection", connectionId)
                .param("key", eventKey)
                .param("type", eventType)
                .param("departed", departed)
                .update() == 1;
    }
}
