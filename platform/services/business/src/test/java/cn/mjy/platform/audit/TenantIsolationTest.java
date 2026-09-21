package cn.mjy.platform.audit;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import cn.mjy.platform.shared.TenantId;
import cn.mjy.platform.shared.tenant.TenantScope;
import java.util.UUID;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.jdbc.core.simple.JdbcClient;

/**
 * P1 门禁"双租户越权全阻断"的数据库层证据：隔离由 PostgreSQL 行级安全强制执行，
 * 而不是靠业务代码记得加 WHERE tenant_id。即便代码写错，数据库也拒绝越权。
 */
@SpringBootTest
class TenantIsolationTest {

    @Autowired
    private TenantScope tenantScope;

    @Autowired
    private AuditLogRepository auditLog;

    @Autowired
    private JdbcClient jdbc;

    private TenantId tenantA;
    private TenantId tenantB;

    @BeforeEach
    void createTenants() {
        tenantA = TenantId.random();
        tenantB = TenantId.random();
    }

    @Test
    void rowsWrittenByOneTenantAreInvisibleToAnother() {
        tenantScope.run(tenantA, () -> auditLog.record(tenantA, "actor-a", "survey.publish", "survey/1", "trace-a"));

        long seenByB = tenantScope.call(tenantB, () -> auditLog.countAll());
        long seenByA = tenantScope.call(tenantA, () -> auditLog.countAll());

        assertThat(seenByB).isZero();
        assertThat(seenByA).isEqualTo(1);
    }

    @Test
    void writingAnotherTenantsRowIsRejectedByTheDatabase() {
        // 模拟一个把租户写错的代码缺陷：在 B 的上下文里写入 A 的行。
        assertThatThrownBy(() -> tenantScope.run(tenantB,
                () -> auditLog.record(tenantA, "actor-b", "survey.publish", "survey/1", "trace-b")))
                .rootCause().hasMessageContaining("row-level security");
    }

    @Test
    void withoutATenantContextNothingIsVisibleAndNothingCanBeWritten() {
        tenantScope.run(tenantA, () -> auditLog.record(tenantA, "actor-a", "login", "user/a", "trace-a"));

        assertThat(auditLog.countAll()).isZero();
        assertThatThrownBy(() -> auditLog.record(tenantA, "actor-a", "login", "user/a", "trace-a"))
                .rootCause().hasMessageContaining("row-level security");
    }

    @Test
    void theApplicationConnectsAsARoleThatCannotBypassRowLevelSecurity() {
        String user = jdbc.sql("SELECT current_user").query(String.class).single();
        boolean bypass = jdbc.sql("SELECT rolbypassrls FROM pg_roles WHERE rolname = current_user")
                .query(Boolean.class).single();
        String owner = jdbc.sql("SELECT tableowner FROM pg_tables WHERE tablename = 'audit_log'")
                .query(String.class).single();

        assertThat(user).isEqualTo("platform_app");
        assertThat(bypass).isFalse();
        assertThat(owner).isNotEqualTo(user);
    }

    @Test
    void tenantContextDoesNotLeakOutOfTheScope() {
        UUID leaked = tenantScope.call(tenantA, () -> tenantScope.currentDatabaseTenant().orElseThrow());

        assertThat(leaked).isEqualTo(tenantA.value());
        assertThat(tenantScope.currentDatabaseTenant()).isEmpty();
    }
}
