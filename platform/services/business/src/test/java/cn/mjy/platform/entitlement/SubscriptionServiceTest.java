package cn.mjy.platform.entitlement;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import cn.mjy.platform.shared.TenantId;
import cn.mjy.platform.shared.tenant.TenantScope;
import java.time.Duration;
import java.time.Instant;
import java.util.List;
import java.util.Map;
import java.util.Set;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.jdbc.core.simple.JdbcClient;

/** 订阅按期追加：换套餐开启新的订阅期，历史期不被改写；到期后状态由时间推导，不靠定时任务改行。 */
@SpringBootTest
class SubscriptionServiceTest {

    @Autowired
    private SubscriptionService subscriptions;

    @Autowired
    private EntitlementFixtures fixtures;

    @Autowired
    private TenantScope tenantScope;

    @Autowired
    private JdbcClient jdbc;

    private final Instant now = Instant.now();

    @Test
    void changingPlanStartsANewPeriodAndNeverRewritesThePastOne() {
        TenantId tenant = TenantId.random();
        PlanVersion basic = fixtures.publish(Set.of(Capabilities.SURVEY_READ), Map.of());
        PlanVersion pro = fixtures.publish(EntitlementFixtures.ALL_CAPABILITIES, Map.of());
        Subscription first = subscriptions.start(tenant, basic.id(), SubscriptionKind.TRIAL,
                now.minus(Duration.ofDays(10)), now.plus(Duration.ofDays(4)));

        Subscription second = subscriptions.start(tenant, pro.id(), SubscriptionKind.PAID,
                now.minus(Duration.ofMinutes(1)), now.plus(Duration.ofDays(365)));

        assertThat(subscriptions.current(tenant)).hasValue(second);
        List<Subscription> history = subscriptions.history(tenant);
        assertThat(history).containsExactly(first, second);
        assertThat(history.get(0).planVersionId()).isEqualTo(basic.id());
        assertThat(history.get(0).endsAt()).isEqualTo(first.endsAt());
    }

    @Test
    void statusIsDerivedFromKindAndTime() {
        TenantId tenant = TenantId.random();
        PlanVersion plan = fixtures.publish(Set.of(Capabilities.SURVEY_READ), Map.of());

        Subscription trial = subscriptions.start(tenant, plan.id(), SubscriptionKind.TRIAL,
                now.minus(Duration.ofDays(1)), now.plus(Duration.ofDays(13)));
        assertThat(trial.statusAt(now)).isEqualTo(SubscriptionStatus.TRIAL);
        assertThat(trial.statusAt(now.plus(Duration.ofDays(14)))).isEqualTo(SubscriptionStatus.EXPIRED);

        Subscription paid = subscriptions.start(tenant, plan.id(), SubscriptionKind.PAID,
                now.minus(Duration.ofSeconds(30)), now.plus(Duration.ofDays(30)));
        assertThat(paid.statusAt(now)).isEqualTo(SubscriptionStatus.ACTIVE);

        Subscription cancelled = subscriptions.cancel(tenant);
        assertThat(cancelled.statusAt(Instant.now())).isEqualTo(SubscriptionStatus.CANCELLED);
        assertThat(cancelled.planVersionId()).isEqualTo(plan.id());
        assertThat(subscriptions.current(tenant)).hasValue(cancelled);
    }

    @Test
    void aTenantWithoutSubscriptionHasNoCurrentOne() {
        assertThat(subscriptions.current(TenantId.random())).isEmpty();
    }

    @Test
    void periodsMustEndAfterTheyStart() {
        PlanVersion plan = fixtures.publish(Set.of(), Map.of());

        assertThatThrownBy(() -> subscriptions.start(TenantId.random(), plan.id(), SubscriptionKind.PAID,
                now, now.minus(Duration.ofDays(1))))
                .isInstanceOf(IllegalArgumentException.class);
    }

    @Test
    void subscriptionPeriodsAreAppendOnlyForTheRuntimeRole() {
        TenantId tenant = fixtures.activeTenantWithQuota(10);

        assertThatThrownBy(() -> tenantScope.run(tenant,
                () -> jdbc.sql("UPDATE subscription SET ends_at = now() + interval '100 years'").update()))
                .rootCause().hasMessageContaining("permission denied");
        assertThatThrownBy(() -> tenantScope.run(tenant,
                () -> jdbc.sql("DELETE FROM subscription").update()))
                .rootCause().hasMessageContaining("permission denied");
    }
}
