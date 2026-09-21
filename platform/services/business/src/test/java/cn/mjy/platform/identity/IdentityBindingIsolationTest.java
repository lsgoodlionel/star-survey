package cn.mjy.platform.identity;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import cn.mjy.platform.shared.TenantId;
import cn.mjy.platform.shared.tenant.TenantScope;
import cn.mjy.platform.tenant.TenantFixtures;
import java.util.UUID;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.dao.DuplicateKeyException;
import org.springframework.jdbc.core.simple.JdbcClient;

/** identity_binding 是租户表：跨租户读写由数据库拒绝；三元组唯一性由数据库约束保证。 */
@SpringBootTest
class IdentityBindingIsolationTest {

    @Autowired
    private IdentityBindingService bindings;

    @Autowired
    private IdentityBindingRepository repository;

    @Autowired
    private TenantFixtures fixtures;

    @Autowired
    private TenantScope tenantScope;

    @Autowired
    private JdbcClient jdbc;

    private TenantId tenantA;
    private TenantId tenantB;
    private IdentityBinding bindingOfA;

    @BeforeEach
    void setUp() {
        tenantA = fixtures.activeTenant();
        tenantB = fixtures.activeTenant();
        ExternalIdentity identity = new ExternalIdentity("wecom", "corp-a", "zhangsan-" + UUID.randomUUID());
        bindingOfA = bindings.bind(tenantA, identity, "user-a", "trace").value();
    }

    private void insertRaw(TenantId scope, TenantId rowTenant, String provider, String appId, String externalId) {
        tenantScope.run(scope, () -> jdbc.sql("""
                        INSERT INTO identity_binding (principal_id, tenant_id, provider, app_id, external_id)
                        VALUES (:id, :tenant, :provider, :app, :external)
                        """)
                .param("id", UUID.randomUUID())
                .param("tenant", rowTenant.value())
                .param("provider", provider)
                .param("app", appId)
                .param("external", externalId)
                .update());
    }

    @Test
    void tenantBCannotReadTenantAsBindings() {
        ExternalIdentity identity = bindingOfA.identity();

        assertThat(tenantScope.call(tenantB, () -> repository.findByPrincipal(bindingOfA.principalId()))).isEmpty();
        assertThat(tenantScope.call(tenantB, () -> repository.findByIdentity(identity))).isEmpty();
        assertThat(tenantScope.call(tenantA, () -> repository.findByIdentity(identity))).contains(bindingOfA);
    }

    @Test
    void tenantBInsertingABindingForTenantAIsRejectedByTheDatabase() {
        assertThatThrownBy(() -> insertRaw(tenantB, tenantA, "wecom", "corp-a", "lisi-" + UUID.randomUUID()))
                .rootCause().hasMessageContaining("row-level security");
    }

    @Test
    void tenantBUpdatingOrDeletingTenantAsBindingIsRejected() {
        assertThatThrownBy(() -> tenantScope.run(tenantB, () -> jdbc
                .sql("UPDATE identity_binding SET tenant_id = :b WHERE principal_id = :id")
                .param("b", tenantB.value())
                .param("id", bindingOfA.principalId()).update()))
                .rootCause().hasMessageContaining("permission denied");
        assertThatThrownBy(() -> tenantScope.run(tenantB, () -> jdbc
                .sql("DELETE FROM identity_binding WHERE principal_id = :id")
                .param("id", bindingOfA.principalId()).update()))
                .rootCause().hasMessageContaining("permission denied");
    }

    @Test
    void theTripleIsUniqueInTheDatabase() {
        ExternalIdentity identity = bindingOfA.identity();

        assertThatThrownBy(() -> insertRaw(tenantA, tenantA, identity.provider(), identity.appId(), identity.externalId()))
                .isInstanceOf(DuplicateKeyException.class);
    }

    @Test
    void theSameExternalIdUnderAnotherAppIsAllowedInTheDatabase() {
        ExternalIdentity identity = bindingOfA.identity();

        insertRaw(tenantA, tenantA, identity.provider(), "corp-other", identity.externalId());

        long rows = tenantScope.call(tenantA, () -> jdbc
                .sql("SELECT COUNT(*) FROM identity_binding WHERE external_id = :e")
                .param("e", identity.externalId()).query(Long.class).single());
        assertThat(rows).isEqualTo(2);
    }

    @Test
    void withoutATenantContextNoBindingIsVisible() {
        assertThat(jdbc.sql("SELECT COUNT(*) FROM identity_binding").query(Long.class).single()).isZero();
    }
}
