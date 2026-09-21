package cn.mjy.platform.entitlement;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import cn.mjy.platform.shared.TenantContext;
import cn.mjy.platform.shared.TenantId;
import cn.mjy.platform.shared.tenant.TenantScope;
import java.time.Duration;
import java.util.UUID;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.jdbc.core.simple.JdbcClient;

/**
 * 额度模块三张租户表（subscription、usage_counter、usage_ledger_entry）的跨租户负面测试。
 * 读写都用直接 SQL 绕过服务层，证明隔离由 PostgreSQL 行级安全强制，而不只是代码里的条件。
 */
@SpringBootTest
class EntitlementTenantIsolationTest {

    @Autowired
    private UsageLedger ledger;

    @Autowired
    private SubscriptionService subscriptions;

    @Autowired
    private EntitlementFixtures fixtures;

    @Autowired
    private TenantScope tenantScope;

    @Autowired
    private JdbcClient jdbc;

    private TenantId tenantA;
    private TenantId tenantB;
    private TenantContext contextA;
    private TenantContext contextB;
    private Subscription subscriptionA;

    @BeforeEach
    void tenantAHasASubscriptionAReservationAndACounter() {
        tenantA = fixtures.activeTenantWithQuota(10);
        tenantB = fixtures.activeTenantWithQuota(10);
        contextA = EntitlementFixtures.contextOf(tenantA);
        contextB = EntitlementFixtures.contextOf(tenantB);
        subscriptionA = subscriptions.current(tenantA).orElseThrow();
        ledger.reserve(contextA, new ReservationRequest("key-a", Capabilities.AI_SURVEY_GENERATE, null, 4,
                "model-x", Duration.ofMinutes(10)));
    }

    @Test
    void tenantBSeesNoneOfTenantAsRows() {
        for (String table : new String[] {"subscription", "usage_counter", "usage_ledger_entry"}) {
            long seenByB = tenantScope.call(tenantB, () -> countOtherTenantRows(table, tenantA));
            long seenByA = tenantScope.call(tenantA, () -> countOtherTenantRows(table, tenantA));

            assertThat(seenByB).as("rows of A visible to B in %s", table).isZero();
            assertThat(seenByA).as("rows of A visible to A in %s", table).isPositive();
        }
    }

    @Test
    void tenantBCannotInsertASubscriptionForTenantA() {
        assertThatThrownBy(() -> tenantScope.run(tenantB, () -> jdbc.sql("""
                INSERT INTO subscription (id, tenant_id, plan_version_id, kind, starts_at, ends_at)
                VALUES (:id, :tenant, :plan, 'paid', now(), now() + interval '100 years')
                """)
                .param("id", UUID.randomUUID())
                .param("tenant", tenantA.value())
                .param("plan", subscriptionA.planVersionId())
                .update()))
                .rootCause().hasMessageContaining("row-level security");
    }

    @Test
    void tenantBCannotWriteLedgerEntriesForTenantA() {
        assertThatThrownBy(() -> tenantScope.run(tenantB, () -> jdbc.sql("""
                INSERT INTO usage_ledger_entry (tenant_id, reservation_key, kind, subscription_id,
                       meter_code, subject, period_key, quantity, actor_id)
                VALUES (:tenant, 'key-a', 'release', :subscription, 'ai.tokens', '', :period, 4, 'attacker')
                """)
                .param("tenant", tenantA.value())
                .param("subscription", subscriptionA.id())
                .param("period", subscriptionA.id().toString())
                .update()))
                .rootCause().hasMessageContaining("row-level security");
    }

    @Test
    void tenantBCannotCreateOrChangeTenantAsCounters() {
        assertThatThrownBy(() -> tenantScope.run(tenantB, () -> jdbc.sql("""
                INSERT INTO usage_counter (tenant_id, meter_code, subject, period_key)
                VALUES (:tenant, 'ai.tokens', '', 'forged')
                """)
                .param("tenant", tenantA.value())
                .update()))
                .rootCause().hasMessageContaining("row-level security");

        int changed = tenantScope.call(tenantB, () -> jdbc.sql(
                        "UPDATE usage_counter SET reserved = 0 WHERE tenant_id = :tenant")
                .param("tenant", tenantA.value())
                .update());

        assertThat(changed).isZero();
        assertThat(ledger.balance(contextA, Meters.AI_TOKENS, null)).isEqualTo(new UsageBalance(4, 0));
    }

    @Test
    void tenantBCannotSettleOrSeeTenantAsReservationThroughTheService() {
        assertThat(ledger.capture(contextB, "key-a", 4).outcome()).isEqualTo(SettlementOutcome.NOT_FOUND);
        assertThat(ledger.release(contextB, "key-a").outcome()).isEqualTo(SettlementOutcome.NOT_FOUND);
        assertThat(ledger.reclaimExpired(tenantB)).isZero();

        assertThat(ledger.balance(contextA, Meters.AI_TOKENS, null)).isEqualTo(new UsageBalance(4, 0));
        assertThat(ledger.balance(contextB, Meters.AI_TOKENS, null)).isEqualTo(UsageBalance.EMPTY);
    }

    @Test
    void theSameReservationKeyInTwoTenantsIsTwoIndependentReservations() {
        ReservationResult b = ledger.reserve(contextB, new ReservationRequest("key-a",
                Capabilities.AI_SURVEY_GENERATE, null, 2, "model-x", Duration.ofMinutes(10)));

        assertThat(b.isGranted()).isTrue();
        assertThat(b.replayed()).isFalse();
        assertThat(ledger.balance(contextB, Meters.AI_TOKENS, null)).isEqualTo(new UsageBalance(2, 0));
    }

    @Test
    void withoutATenantContextNoEntitlementDataIsVisible() {
        for (String table : new String[] {"subscription", "usage_counter", "usage_ledger_entry"}) {
            long visible = jdbc.sql("SELECT COUNT(*) FROM " + table).query(Long.class).single();

            assertThat(visible).as("rows visible without tenant in %s", table).isZero();
        }
    }

    private long countOtherTenantRows(String table, TenantId owner) {
        return jdbc.sql("SELECT COUNT(*) FROM " + table + " WHERE tenant_id = :tenant")
                .param("tenant", owner.value())
                .query(Long.class).single();
    }
}
