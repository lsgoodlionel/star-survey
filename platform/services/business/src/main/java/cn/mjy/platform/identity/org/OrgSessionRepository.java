package cn.mjy.platform.identity.org;

import cn.mjy.platform.shared.TenantId;
import java.time.Instant;
import java.util.UUID;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.stereotype.Repository;

/** org_login_session：免登签发的令牌对应的会话。必须在 {@code TenantScope} 内调用。 */
@Repository
class OrgSessionRepository {

    static final String REASON_DEPARTED = "departed";
    static final String REASON_LOGOUT = "logout";

    private final JdbcClient jdbc;

    OrgSessionRepository(JdbcClient jdbc) {
        this.jdbc = jdbc;
    }

    void insert(UUID sessionId, TenantId tenant, UUID principalId, UUID connectionId, Instant issuedAt,
            Instant expiresAt) {
        jdbc.sql("""
                        INSERT INTO org_login_session (id, tenant_id, principal_id, connection_id, issued_at, expires_at)
                        VALUES (:id, :tenant, :principal, :connection, :issued, :expires)
                        """)
                .param("id", sessionId)
                .param("tenant", tenant.value())
                .param("principal", principalId)
                .param("connection", connectionId)
                .param("issued", java.sql.Timestamp.from(issuedAt))
                .param("expires", java.sql.Timestamp.from(expiresAt))
                .update();
    }

    /** 会话属于该主体、未撤销、未过期，且主体的身份绑定未被撤销。 */
    boolean isLive(UUID sessionId, UUID principalId) {
        return jdbc.sql("""
                        SELECT EXISTS (
                            SELECT 1 FROM org_login_session s
                            WHERE s.id = :id AND s.principal_id = :principal
                              AND s.revoked_at IS NULL AND s.expires_at > now()
                              AND NOT EXISTS (SELECT 1 FROM identity_binding_revocation r
                                              WHERE r.principal_id = s.principal_id
                                                AND r.reinstated_at IS NULL))
                        """)
                .param("id", sessionId)
                .param("principal", principalId)
                .query(Boolean.class)
                .single();
    }

    int revokeAllOf(UUID principalId, String reason) {
        return jdbc.sql("""
                        UPDATE org_login_session SET revoked_at = now(), revoke_reason = :reason
                        WHERE principal_id = :principal AND revoked_at IS NULL
                        """)
                .param("principal", principalId)
                .param("reason", reason)
                .update();
    }

    int revoke(UUID sessionId, UUID principalId, String reason) {
        return jdbc.sql("""
                        UPDATE org_login_session SET revoked_at = now(), revoke_reason = :reason
                        WHERE id = :id AND principal_id = :principal AND revoked_at IS NULL
                        """)
                .param("id", sessionId)
                .param("principal", principalId)
                .param("reason", reason)
                .update();
    }
}
