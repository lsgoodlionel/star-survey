package cn.mjy.platform.identity.org;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import cn.mjy.platform.shared.TenantId;
import java.util.UUID;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.ValueSource;

/** 新表的行级安全（原始 SQL）：B 读不到 A 的行、写不进 A 的行；无租户上下文什么都看不到；只追加表不能改删。 */
class OrgTablesIsolationTest extends OrgLoginTestSupport {

    private TenantId tenantA;
    private TenantId tenantB;
    private Org orgA;

    @BeforeEach
    void setUp() throws Exception {
        tenantA = newTenant();
        tenantB = newTenant();
        orgA = connect(tenantA, "wecom", "jit_staff");
        String token = login(orgA, unique("u"));
        start(tenantA, orgA.connectionId());
        tenantScope.run(tenantA, () -> jdbc.sql("""
                        INSERT INTO identity_binding_revocation (principal_id, tenant_id, reason, revoked_by)
                        VALUES (:p, :t, 'admin', 'test')
                        """)
                .param("p", UUID.fromString(subject(token))).param("t", tenantA.value()).update());
    }

    private long count(TenantId scope, String table) {
        return tenantScope.call(scope, () -> jdbc.sql("SELECT COUNT(*) FROM " + table).query(Long.class).single());
    }

    @ParameterizedTest
    @ValueSource(strings = {"org_connection", "org_login_state", "org_login_session", "identity_binding_revocation"})
    void tenantBCannotReadTenantAsRows(String table) {
        assertThat(count(tenantA, table)).isPositive();
        assertThat(count(tenantB, table)).isZero();
        assertThat(jdbc.sql("SELECT COUNT(*) FROM " + table).query(Long.class).single()).isZero();
    }

    @Test
    void tenantBCannotInsertConnectionsForTenantA() {
        assertThatThrownBy(() -> tenantScope.run(tenantB, () -> jdbc.sql("""
                        INSERT INTO org_connection (id, tenant_id, provider, corp_id, app_id, secret_ref,
                                                    unknown_user_policy, enabled, created_by)
                        VALUES (:id, :t, 'wecom', 'c', 'a', 'REF', 'jit_staff', true, 'x')
                        """)
                .param("id", UUID.randomUUID()).param("t", tenantA.value()).update()))
                .rootCause().hasMessageContaining("row-level security");
    }

    @Test
    void tenantBCannotInsertStatesOrSessionsForTenantA() {
        assertThatThrownBy(() -> tenantScope.run(tenantB, () -> jdbc.sql("""
                        INSERT INTO org_login_state (state, tenant_id, connection_id, browser_hash, expires_at)
                        VALUES (:s, :t, :c, :h, now() + interval '5 minutes')
                        """)
                .param("s", "a".repeat(40)).param("t", tenantA.value()).param("c", orgA.connectionId())
                .param("h", "0".repeat(64)).update()))
                .rootCause().hasMessageContaining("row-level security");
    }

    @Test
    void aTenantBRowCannotPointAtTenantAsConnectionOrPrincipal() {
        // 行级安全只校验子行自身的 tenant_id；外键校验绕过行级安全，所以引用必须带上 tenant_id。
        assertThatThrownBy(() -> tenantScope.run(tenantB, () -> jdbc.sql("""
                        INSERT INTO org_login_state (state, tenant_id, connection_id, browser_hash, expires_at)
                        VALUES (:s, :t, :c, :h, now() + interval '5 minutes')
                        """)
                .param("s", UUID.randomUUID().toString().replace("-", "")).param("t", tenantB.value()).param("c", orgA.connectionId())
                .param("h", "0".repeat(64)).update()))
                .rootCause().hasMessageContaining("foreign key");
        UUID principalOfA = tenantScope.call(tenantA, () -> jdbc
                .sql("SELECT principal_id FROM identity_binding_revocation").query(UUID.class).list().getFirst());
        assertThatThrownBy(() -> tenantScope.run(tenantB, () -> jdbc.sql("""
                        INSERT INTO org_login_session (id, tenant_id, principal_id, connection_id, expires_at)
                        VALUES (:id, :t, :p, :c, now() + interval '5 minutes')
                        """)
                .param("id", UUID.randomUUID()).param("t", tenantB.value()).param("p", principalOfA)
                .param("c", orgA.connectionId()).update()))
                .rootCause().hasMessageContaining("foreign key");
    }

    @Test
    void tenantBCannotReviveOrDisableTenantAsRows() {
        int sessions = tenantScope.call(tenantB, () -> jdbc
                .sql("UPDATE org_login_session SET revoked_at = NULL, revoke_reason = NULL").update());
        int connections = tenantScope.call(tenantB, () -> jdbc
                .sql("UPDATE org_connection SET enabled = false").update());

        assertThat(sessions).isZero();
        assertThat(connections).isZero();
    }

    @Test
    void revocationsAndSessionsCannotBeDeletedAndConnectionsCannotBeDeleted() {
        assertThatThrownBy(() -> tenantScope.run(tenantA, () -> jdbc
                .sql("DELETE FROM identity_binding_revocation").update()))
                .rootCause().hasMessageContaining("permission denied");
        assertThatThrownBy(() -> tenantScope.run(tenantA, () -> jdbc
                .sql("UPDATE identity_binding_revocation SET reason = 'admin'").update()))
                .rootCause().hasMessageContaining("permission denied");
        assertThatThrownBy(() -> tenantScope.run(tenantA, () -> jdbc
                .sql("DELETE FROM org_login_session").update()))
                .rootCause().hasMessageContaining("permission denied");
        assertThatThrownBy(() -> tenantScope.run(tenantA, () -> jdbc
                .sql("DELETE FROM org_connection").update()))
                .rootCause().hasMessageContaining("permission denied");
    }
}
