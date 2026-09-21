package cn.mjy.platform.identity;

import cn.mjy.platform.shared.TenantId;
import java.sql.ResultSet;
import java.sql.SQLException;
import java.util.Optional;
import java.util.UUID;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.stereotype.Repository;

/** identity_binding 表的读写。必须在 {@code TenantScope} 内调用，可见范围由行级安全决定。 */
@Repository
public class IdentityBindingRepository {

    private static final String COLUMNS = "principal_id, tenant_id, provider, app_id, external_id";

    private final JdbcClient jdbc;

    public IdentityBindingRepository(JdbcClient jdbc) {
        this.jdbc = jdbc;
    }

    /** 新建绑定；三元组已存在（无论属于哪个租户）时什么都不做并返回空。 */
    public Optional<IdentityBinding> insertIfAbsent(TenantId tenant, ExternalIdentity identity) {
        return jdbc.sql("""
                        INSERT INTO identity_binding (principal_id, tenant_id, provider, app_id, external_id)
                        VALUES (:id, :tenant, :provider, :app, :external)
                        ON CONFLICT (tenant_id, provider, app_id, external_id) DO NOTHING
                        RETURNING %s
                        """.formatted(COLUMNS))
                .param("id", UUID.randomUUID())
                .param("tenant", tenant.value())
                .param("provider", identity.provider())
                .param("app", identity.appId())
                .param("external", identity.externalId())
                .query(IdentityBindingRepository::map)
                .optional();
    }

    public Optional<IdentityBinding> findByIdentity(ExternalIdentity identity) {
        return jdbc.sql("""
                        SELECT %s FROM identity_binding
                        WHERE provider = :provider AND app_id = :app AND external_id = :external
                        """.formatted(COLUMNS))
                .param("provider", identity.provider())
                .param("app", identity.appId())
                .param("external", identity.externalId())
                .query(IdentityBindingRepository::map)
                .optional();
    }

    public Optional<IdentityBinding> findByPrincipal(UUID principalId) {
        return jdbc.sql("SELECT " + COLUMNS + " FROM identity_binding WHERE principal_id = :id")
                .param("id", principalId)
                .query(IdentityBindingRepository::map)
                .optional();
    }

    private static IdentityBinding map(ResultSet rs, int row) throws SQLException {
        return new IdentityBinding(
                rs.getObject("principal_id", UUID.class),
                new TenantId(rs.getObject("tenant_id", UUID.class)),
                new ExternalIdentity(rs.getString("provider"), rs.getString("app_id"), rs.getString("external_id")));
    }
}
