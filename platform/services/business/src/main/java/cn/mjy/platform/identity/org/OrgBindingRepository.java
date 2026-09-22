package cn.mjy.platform.identity.org;

import cn.mjy.platform.shared.TenantId;
import java.util.List;
import java.util.UUID;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.stereotype.Repository;

/**
 * 组织身份绑定的撤销、恢复与按组织分页列举（identity_binding + identity_binding_revocation，同属身份模块）。
 * "已撤销" = 存在一条未恢复的撤销（V110）。必须在 {@code TenantScope} 内调用。
 */
@Repository
class OrgBindingRepository {

    record ActiveBinding(UUID principalId, String externalId, java.time.Instant createdAt) {
    }

    /** 分页游标：上一页最后一行的 (created_at, principal_id)；首页为空。 */
    record Cursor(java.time.Instant createdAt, UUID principalId) {
    }

    static final String REASON_DEPARTED = "departed";

    private final JdbcClient jdbc;

    OrgBindingRepository(JdbcClient jdbc) {
        this.jdbc = jdbc;
    }

    boolean isRevoked(UUID principalId) {
        return jdbc.sql("""
                        SELECT EXISTS (SELECT 1 FROM identity_binding_revocation
                                       WHERE principal_id = :p AND reinstated_at IS NULL)
                        """)
                .param("p", principalId)
                .query(Boolean.class)
                .single();
    }

    /** 记一条撤销；已有未恢复的撤销时什么都不做。返回是否为本次新撤销。 */
    boolean revoke(TenantId tenant, UUID principalId, String reason, String revokedBy) {
        return jdbc.sql("""
                        INSERT INTO identity_binding_revocation (principal_id, tenant_id, reason, revoked_by)
                        VALUES (:p, :tenant, :reason, :by)
                        ON CONFLICT (tenant_id, principal_id) WHERE reinstated_at IS NULL DO NOTHING
                        """)
                .param("p", principalId)
                .param("tenant", tenant.value())
                .param("reason", reason)
                .param("by", revokedBy)
                .update() == 1;
    }

    /** 恢复：在未恢复的撤销上盖恢复戳。返回是否恢复了一条（没有未恢复的撤销时为假）。 */
    boolean reinstate(UUID principalId, String reinstatedBy) {
        return jdbc.sql("""
                        UPDATE identity_binding_revocation SET reinstated_at = now(), reinstated_by = :by
                        WHERE principal_id = :p AND reinstated_at IS NULL
                        """)
                .param("p", principalId)
                .param("by", reinstatedBy)
                .update() == 1;
    }

    /** 某组织作用域下仍有效（未撤销）的绑定，按 (建立时间, 主体) 键集分页，after 为空取首页。 */
    List<ActiveBinding> listActivePage(String provider, String corpId, Cursor after, int limit) {
        return jdbc.sql("""
                        SELECT b.principal_id, b.external_id, b.created_at FROM identity_binding b
                        WHERE b.provider = :provider AND b.app_id = :corp
                          AND (CAST(:afterAt AS timestamptz) IS NULL
                               OR (b.created_at, b.principal_id) > (CAST(:afterAt AS timestamptz), CAST(:afterId AS uuid)))
                          AND NOT EXISTS (SELECT 1 FROM identity_binding_revocation r
                                          WHERE r.principal_id = b.principal_id AND r.reinstated_at IS NULL)
                        ORDER BY b.created_at, b.principal_id
                        LIMIT :limit
                        """)
                .param("provider", provider)
                .param("corp", corpId)
                .param("afterAt", after == null ? null : after.createdAt().atOffset(java.time.ZoneOffset.UTC),
                        java.sql.Types.TIMESTAMP_WITH_TIMEZONE)
                .param("afterId", after == null ? null : after.principalId(), java.sql.Types.OTHER)
                .param("limit", limit)
                .query((rs, n) -> new ActiveBinding(rs.getObject("principal_id", UUID.class),
                        rs.getString("external_id"), rs.getTimestamp("created_at").toInstant()))
                .list();
    }
}
