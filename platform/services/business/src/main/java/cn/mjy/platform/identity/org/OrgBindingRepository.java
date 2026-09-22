package cn.mjy.platform.identity.org;

import cn.mjy.platform.shared.TenantId;
import java.util.List;
import java.util.UUID;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.stereotype.Repository;

/**
 * 组织身份绑定的撤销与按组织列举（identity_binding + identity_binding_revocation，同属身份模块）。
 * 必须在 {@code TenantScope} 内调用。
 */
@Repository
class OrgBindingRepository {

    record ActiveBinding(UUID principalId, String externalId) {
    }

    static final String REASON_DEPARTED = "departed";

    private final JdbcClient jdbc;

    OrgBindingRepository(JdbcClient jdbc) {
        this.jdbc = jdbc;
    }

    boolean isRevoked(UUID principalId) {
        return jdbc.sql("SELECT EXISTS (SELECT 1 FROM identity_binding_revocation WHERE principal_id = :p)")
                .param("p", principalId)
                .query(Boolean.class)
                .single();
    }

    /** 记一条撤销；已撤销时什么都不做。返回是否为本次新撤销。 */
    boolean revoke(TenantId tenant, UUID principalId, String reason, String revokedBy) {
        return jdbc.sql("""
                        INSERT INTO identity_binding_revocation (principal_id, tenant_id, reason, revoked_by)
                        VALUES (:p, :tenant, :reason, :by)
                        ON CONFLICT (principal_id) DO NOTHING
                        """)
                .param("p", principalId)
                .param("tenant", tenant.value())
                .param("reason", reason)
                .param("by", revokedBy)
                .update() == 1;
    }

    /** 某组织作用域下仍有效（未撤销）的绑定，按建立顺序。 */
    List<ActiveBinding> listActive(String provider, String corpId) {
        return jdbc.sql("""
                        SELECT b.principal_id, b.external_id FROM identity_binding b
                        WHERE b.provider = :provider AND b.app_id = :corp
                          AND NOT EXISTS (SELECT 1 FROM identity_binding_revocation r
                                          WHERE r.principal_id = b.principal_id)
                        ORDER BY b.created_at, b.principal_id
                        """)
                .param("provider", provider)
                .param("corp", corpId)
                .query((rs, n) -> new ActiveBinding(rs.getObject("principal_id", UUID.class),
                        rs.getString("external_id")))
                .list();
    }
}
