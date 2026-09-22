package cn.mjy.platform.identity.org;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import cn.mjy.platform.shared.TenantId;
import java.util.UUID;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;

/**
 * V109 / V110 的数据库约束（原始 SQL，运行期账号）：事件回执的行级安全、复合外键与只追加；
 * 撤销表的恢复戳只能盖一次、撤销事实不可改、同一主体同时至多一条未恢复的撤销。
 */
class OrgDirectoryTablesIsolationTest extends OrgLoginTestSupport {

    private static final String HASH = "a".repeat(64);

    private TenantId tenantA;
    private TenantId tenantB;
    private Org orgA;
    private Org orgB;
    private UUID principalA;

    @BeforeEach
    void setUp() throws Exception {
        tenantA = newTenant();
        tenantB = newTenant();
        orgA = connect(tenantA, "wecom", "jit_staff");
        orgB = connect(tenantB, "wecom", "jit_staff");
        principalA = UUID.fromString(subject(login(orgA, unique("u"))));
        tenantScope.run(tenantA, () -> insertReceipt(tenantA, orgA.connectionId(), HASH));
    }

    private void insertReceipt(TenantId tenant, UUID connection, String key) {
        jdbc.sql("""
                        INSERT INTO org_event_receipt (tenant_id, connection_id, event_key, event_type, departed)
                        VALUES (:t, :c, :k, 'test', 0)
                        """)
                .param("t", tenant.value()).param("c", connection).param("k", key).update();
    }

    private void revoke(TenantId tenant, UUID principal) {
        jdbc.sql("""
                        INSERT INTO identity_binding_revocation (principal_id, tenant_id, reason, revoked_by)
                        VALUES (:p, :t, 'departed', 'test')
                        """)
                .param("p", principal).param("t", tenant.value()).update();
    }

    private long count(TenantId scope, String table) {
        return tenantScope.call(scope, () -> jdbc.sql("SELECT COUNT(*) FROM " + table).query(Long.class).single());
    }

    @Test
    void tenantBCannotReadTenantAsReceipts() {
        assertThat(count(tenantA, "org_event_receipt")).isEqualTo(1);
        assertThat(count(tenantB, "org_event_receipt")).isZero();
        assertThat(jdbc.sql("SELECT COUNT(*) FROM org_event_receipt").query(Long.class).single()).isZero();
    }

    @Test
    void tenantBCannotWriteReceiptsForTenantA() {
        assertThatThrownBy(() -> tenantScope.run(tenantB, () -> insertReceipt(tenantA, orgA.connectionId(),
                "b".repeat(64))))
                .rootCause().hasMessageContaining("row-level security");
    }

    @Test
    void aTenantBReceiptCannotPointAtTenantAsConnection() {
        assertThatThrownBy(() -> tenantScope.run(tenantB, () -> insertReceipt(tenantB, orgA.connectionId(),
                "c".repeat(64))))
                .rootCause().hasMessageContaining("foreign key");
        tenantScope.run(tenantB, () -> insertReceipt(tenantB, orgB.connectionId(), "c".repeat(64)));
    }

    @Test
    void receiptsAreAppendOnlyAndKeysAreHashes() {
        assertThatThrownBy(() -> tenantScope.run(tenantA, () -> jdbc
                .sql("DELETE FROM org_event_receipt").update()))
                .rootCause().hasMessageContaining("permission denied");
        assertThatThrownBy(() -> tenantScope.run(tenantA, () -> jdbc
                .sql("UPDATE org_event_receipt SET departed = 1").update()))
                .rootCause().hasMessageContaining("permission denied");
        assertThatThrownBy(() -> tenantScope.run(tenantA, () -> insertReceipt(tenantA, orgA.connectionId(),
                "raw-event-id")))
                .rootCause().hasMessageContaining("check constraint");
    }

    @Test
    void aReinstatementStampIsSetOnceAndRevocationFactsAreImmutable() {
        tenantScope.run(tenantA, () -> revoke(tenantA, principalA));
        String stamp = "UPDATE identity_binding_revocation SET reinstated_at = now(), reinstated_by = 'admin'";

        assertThat(tenantScope.call(tenantA, () -> jdbc.sql(stamp).update())).isEqualTo(1);
        assertThatThrownBy(() -> tenantScope.run(tenantA, () -> jdbc.sql(stamp).update()))
                .rootCause().hasMessageContaining("already reinstated");
        assertThatThrownBy(() -> tenantScope.run(tenantA, () -> jdbc
                .sql("UPDATE identity_binding_revocation SET reinstated_at = NULL, reinstated_by = NULL").update()))
                .rootCause().hasMessageContaining("already reinstated");
        assertThatThrownBy(() -> tenantScope.run(tenantA, () -> jdbc
                .sql("UPDATE identity_binding_revocation SET reason = 'admin'").update()))
                .rootCause().hasMessageContaining("permission denied");
        assertThatThrownBy(() -> tenantScope.run(tenantA, () -> jdbc
                .sql("DELETE FROM identity_binding_revocation").update()))
                .rootCause().hasMessageContaining("permission denied");
    }

    @Test
    void aPrincipalHasAtMostOneOpenRevocationButMayBeRevokedAgainAfterReinstatement() {
        tenantScope.run(tenantA, () -> revoke(tenantA, principalA));

        assertThatThrownBy(() -> tenantScope.run(tenantA, () -> revoke(tenantA, principalA)))
                .rootCause().hasMessageContaining("duplicate key");
        tenantScope.run(tenantA, () -> jdbc.sql(
                "UPDATE identity_binding_revocation SET reinstated_at = now(), reinstated_by = 'admin'").update());
        tenantScope.run(tenantA, () -> revoke(tenantA, principalA));
        assertThat(count(tenantA, "identity_binding_revocation")).isEqualTo(2);
    }

    @Test
    void tenantBCannotReinstateTenantAsRevocations() {
        tenantScope.run(tenantA, () -> revoke(tenantA, principalA));

        int changed = tenantScope.call(tenantB, () -> jdbc.sql(
                "UPDATE identity_binding_revocation SET reinstated_at = now(), reinstated_by = 'b'").update());

        assertThat(changed).isZero();
        assertThat(tenantScope.call(tenantA, () -> jdbc.sql(
                "SELECT COUNT(*) FROM identity_binding_revocation WHERE reinstated_at IS NULL")
                .query(Long.class).single())).isEqualTo(1);
    }
}
