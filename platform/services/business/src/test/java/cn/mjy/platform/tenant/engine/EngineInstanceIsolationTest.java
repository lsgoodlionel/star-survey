package cn.mjy.platform.tenant.engine;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import cn.mjy.platform.shared.TenantId;
import cn.mjy.platform.shared.tenant.TenantScope;
import cn.mjy.platform.tenant.TenantFixtures;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.jdbc.core.simple.JdbcClient;

/** engine_instance 是租户表：跨租户读写由 PostgreSQL 行级安全拒绝，不靠业务代码。 */
@SpringBootTest
class EngineInstanceIsolationTest {

    @Autowired
    private EngineInstanceService instances;

    @Autowired
    private EngineInstanceRepository repository;

    @Autowired
    private TenantFixtures fixtures;

    @Autowired
    private TenantScope tenantScope;

    @Autowired
    private JdbcClient jdbc;

    private TenantId tenantA;
    private TenantId tenantB;
    private String instanceOfA;

    @BeforeEach
    void setUp() {
        tenantA = fixtures.activeTenant();
        tenantB = fixtures.activeTenant();
        instanceOfA = EngineFixtures.uniqueInstanceId();
        instances.register(tenantA, instanceOfA, "https://a.engine.example", "op", "trace");
    }

    private String statusOfAsInstance() {
        return tenantScope.call(tenantA, () -> repository.find(instanceOfA).orElseThrow().status().code());
    }

    @Test
    void tenantBCannotReadTenantAsInstances() {
        assertThat(tenantScope.call(tenantB, () -> repository.find(instanceOfA))).isEmpty();
        assertThat(tenantScope.call(tenantB, () -> repository.listAll())).isEmpty();
        assertThat(tenantScope.call(tenantA, () -> repository.listAll()))
                .extracting(EngineInstance::id).containsExactly(instanceOfA);
    }

    @Test
    void tenantBInsertingARowForTenantAIsRejectedByTheDatabase() {
        assertThatThrownBy(() -> tenantScope.run(tenantB, () -> jdbc.sql("""
                        INSERT INTO engine_instance (id, tenant_id, base_url) VALUES (:id, :tenant, 'https://x.example')
                        """)
                .param("id", EngineFixtures.uniqueInstanceId())
                .param("tenant", tenantA.value())
                .update()))
                .rootCause().hasMessageContaining("row-level security");
    }

    @Test
    void tenantBUpdatingTenantAsRowChangesNothing() {
        int rows = tenantScope.call(tenantB, () -> jdbc
                .sql("UPDATE engine_instance SET status = 'retired' WHERE id = :id")
                .param("id", instanceOfA)
                .update());

        assertThat(rows).isZero();
        assertThat(statusOfAsInstance()).isEqualTo("active");
    }

    @Test
    void anInstanceCannotBeReassignedToAnotherTenant() {
        // 运行期账号只能改 status 列：即便在 A 自己的上下文里，也不能把行改到 B 名下。
        assertThatThrownBy(() -> tenantScope.run(tenantA, () -> jdbc
                .sql("UPDATE engine_instance SET tenant_id = :b WHERE id = :id")
                .param("b", tenantB.value())
                .param("id", instanceOfA)
                .update()))
                .rootCause().hasMessageContaining("permission denied");
    }

    @Test
    void withoutATenantContextNoInstanceIsVisible() {
        assertThat(jdbc.sql("SELECT COUNT(*) FROM engine_instance").query(Long.class).single()).isZero();
    }

    @Test
    void theRuntimeAccountCannotDeleteInstances() {
        assertThatThrownBy(() -> tenantScope.run(tenantA, () -> jdbc
                .sql("DELETE FROM engine_instance WHERE id = :id").param("id", instanceOfA).update()))
                .rootCause().hasMessageContaining("permission denied");
    }
}
