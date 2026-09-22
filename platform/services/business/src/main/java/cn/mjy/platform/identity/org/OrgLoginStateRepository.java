package cn.mjy.platform.identity.org;

import cn.mjy.platform.shared.TenantId;
import java.time.Instant;
import java.util.Optional;
import java.util.UUID;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.stereotype.Repository;

/** org_login_state：一次性 state。必须在 {@code TenantScope} 内调用。 */
@Repository
class OrgLoginStateRepository {

    /** 已被消费（烧掉）的 state 的内容；是否可用由调用方核对连接、浏览器与有效期。 */
    record ConsumedState(UUID connectionId, String browserHash, Instant expiresAt) {
    }

    /** 已消费或过期超过这么久的 state 在下次发起登录时清理。 */
    private static final String RETENTION = "1 day";

    private final JdbcClient jdbc;

    OrgLoginStateRepository(JdbcClient jdbc) {
        this.jdbc = jdbc;
    }

    void insert(TenantId tenant, String state, UUID connectionId, String browserHash, Instant expiresAt) {
        jdbc.sql("""
                        INSERT INTO org_login_state (state, tenant_id, connection_id, browser_hash, expires_at)
                        VALUES (:state, :tenant, :connection, :hash, :expires)
                        """)
                .param("state", state)
                .param("tenant", tenant.value())
                .param("connection", connectionId)
                .param("hash", browserHash)
                .param("expires", java.sql.Timestamp.from(expiresAt))
                .update();
    }

    /**
     * 原子地烧掉 state：只有尚未消费的 state 会返回内容，并发的两次回调恰好一次拿到。
     * 无论之后核对是否通过，state 都已作废——被截获的 state 最多被试一次。
     */
    Optional<ConsumedState> consume(String state) {
        return jdbc.sql("""
                        UPDATE org_login_state SET consumed_at = now()
                        WHERE state = :state AND consumed_at IS NULL
                        RETURNING connection_id, browser_hash, expires_at
                        """)
                .param("state", state)
                .query((rs, n) -> new ConsumedState(rs.getObject("connection_id", UUID.class),
                        rs.getString("browser_hash"), rs.getTimestamp("expires_at").toInstant()))
                .optional();
    }

    void purgeStale() {
        jdbc.sql("DELETE FROM org_login_state WHERE expires_at < now() - interval '" + RETENTION + "'").update();
    }
}
