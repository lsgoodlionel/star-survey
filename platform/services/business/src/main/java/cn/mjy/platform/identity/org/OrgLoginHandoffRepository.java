package cn.mjy.platform.identity.org;

import cn.mjy.platform.shared.TenantId;
import java.time.Instant;
import java.util.Optional;
import java.util.UUID;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.stereotype.Repository;

/** 管理端登录的一次性交接。所有方法都必须在对应的 {@code TenantScope} 内调用。 */
@Repository
class OrgLoginHandoffRepository {

    record PendingHandoff(UUID principalId, UUID connectionId, OrgProvider provider, Instant expiresAt) {
    }

    private final JdbcClient jdbc;

    OrgLoginHandoffRepository(JdbcClient jdbc) {
        this.jdbc = jdbc;
    }

    void insert(TenantId tenant, String handoffHash, String browserHash, UUID principalId, UUID connectionId,
            OrgProvider provider, Instant expiresAt) {
        jdbc.sql("""
                        INSERT INTO org_login_handoff
                            (handoff_hash, browser_hash, tenant_id, principal_id, connection_id, provider, expires_at)
                        VALUES (:handoff, :browser, :tenant, :principal, :connection, :provider, :expires)
                        """)
                .param("handoff", handoffHash)
                .param("browser", browserHash)
                .param("tenant", tenant.value())
                .param("principal", principalId)
                .param("connection", connectionId)
                .param("provider", provider.code())
                .param("expires", java.sql.Timestamp.from(expiresAt))
                .update();
    }

    Optional<PendingHandoff> consume(TenantId tenant, String handoffHash, String browserHash, Instant now) {
        return jdbc.sql("SELECT * FROM org_login_handoff_consume(:tenant, :handoff, :browser, :now)")
                .param("tenant", tenant.value())
                .param("handoff", handoffHash)
                .param("browser", browserHash)
                .param("now", java.sql.Timestamp.from(now))
                .query((rs, n) -> new PendingHandoff(
                        rs.getObject("principal_id", UUID.class),
                        rs.getObject("connection_id", UUID.class),
                        OrgProvider.fromCode(rs.getString("provider")).orElseThrow(),
                        rs.getTimestamp("expires_at").toInstant()))
                .optional();
    }

    int purgeStale(TenantId tenant, Instant now) {
        return jdbc.sql("SELECT org_login_handoff_purge_stale(:tenant, :now)")
                .param("tenant", tenant.value())
                .param("now", java.sql.Timestamp.from(now))
                .query(Integer.class)
                .single();
    }
}
