package cn.mjy.platform.entitlement;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import cn.mjy.platform.shared.TenantContext;
import cn.mjy.platform.shared.TenantId;
import java.time.Duration;
import java.time.Instant;
import java.util.Map;
import java.util.Set;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;

/**
 * 额度判定：ALLOW / DENY / NEED_UPGRADE 加机器可读原因。
 * 到期（或取消）后只读：可读、导出窗口内可导出，写一律拒绝；数据从不自动删除（P-04）。
 */
@SpringBootTest
class EntitlementDecisionTest {

    @Autowired
    private EntitlementService entitlements;

    @Autowired
    private SubscriptionService subscriptions;

    @Autowired
    private EntitlementFixtures fixtures;

    private final Instant now = Instant.now();

    @Test
    void aCapabilityInTheActivePlanIsAllowed() {
        TenantContext ctx = EntitlementFixtures.contextOf(fixtures.activeTenantWithQuota(10));

        assertThat(entitlements.decide(ctx, Capabilities.SURVEY_WRITE, 1))
                .isEqualTo(Decision.allow());
    }

    @Test
    void aCapabilityMissingFromThePlanNeedsAnUpgrade() {
        TenantContext ctx = EntitlementFixtures.contextOf(
                fixtures.activeTenant(Set.of(Capabilities.SURVEY_READ), Map.of()));

        Decision decision = entitlements.decide(ctx, Capabilities.AI_SURVEY_GENERATE, 1);

        assertThat(decision.outcome()).isEqualTo(Outcome.NEED_UPGRADE);
        assertThat(decision.reason()).isEqualTo(DecisionReason.CAPABILITY_NOT_IN_PLAN);
    }

    @Test
    void aTenantWithoutSubscriptionIsDenied() {
        Decision decision = entitlements.decide(EntitlementFixtures.contextOf(TenantId.random()),
                Capabilities.SURVEY_READ, 1);

        assertThat(decision).isEqualTo(Decision.deny(DecisionReason.NO_SUBSCRIPTION));
    }

    @Test
    void anUnknownCapabilityIsDenied() {
        TenantContext ctx = EntitlementFixtures.contextOf(fixtures.activeTenantWithQuota(10));

        assertThat(entitlements.decide(ctx, "survey.teleport", 1))
                .isEqualTo(Decision.deny(DecisionReason.UNKNOWN_CAPABILITY));
    }

    @Test
    void anExpiredSubscriptionAllowsReadsAndExportsButDeniesWrites() {
        TenantContext ctx = EntitlementFixtures.contextOf(tenantExpiredDaysAgo(3, 30));

        assertThat(entitlements.decide(ctx, Capabilities.SURVEY_READ, 1)).isEqualTo(Decision.allow());
        assertThat(entitlements.decide(ctx, Capabilities.RESPONSE_EXPORT, 1)).isEqualTo(Decision.allow());
        assertThat(entitlements.decide(ctx, Capabilities.SURVEY_WRITE, 1))
                .isEqualTo(Decision.deny(DecisionReason.SUBSCRIPTION_READ_ONLY));
        assertThat(entitlements.decide(ctx, Capabilities.RESPONSE_COLLECT, "survey-1", 1))
                .isEqualTo(Decision.deny(DecisionReason.SUBSCRIPTION_READ_ONLY));
        assertThat(entitlements.decide(ctx, Capabilities.AI_RESPONSE_ANALYZE, 1))
                .isEqualTo(Decision.deny(DecisionReason.SUBSCRIPTION_READ_ONLY));
    }

    @Test
    void afterTheExportWindowReadsStillWorkButExportsAreDenied() {
        TenantContext ctx = EntitlementFixtures.contextOf(tenantExpiredDaysAgo(40, 30));

        assertThat(entitlements.decide(ctx, Capabilities.SURVEY_READ, 1)).isEqualTo(Decision.allow());
        assertThat(entitlements.decide(ctx, Capabilities.RESPONSE_EXPORT, 1))
                .isEqualTo(Decision.deny(DecisionReason.EXPORT_WINDOW_CLOSED));
    }

    @Test
    void aCancelledSubscriptionIsReadOnlyToo() {
        TenantId tenant = fixtures.activeTenantWithQuota(10);
        subscriptions.cancel(tenant);
        TenantContext ctx = EntitlementFixtures.contextOf(tenant);

        assertThat(entitlements.decide(ctx, Capabilities.SURVEY_READ, 1)).isEqualTo(Decision.allow());
        assertThat(entitlements.decide(ctx, Capabilities.RESPONSE_EXPORT, 1)).isEqualTo(Decision.allow());
        assertThat(entitlements.decide(ctx, Capabilities.SURVEY_WRITE, 1))
                .isEqualTo(Decision.deny(DecisionReason.SUBSCRIPTION_READ_ONLY));
    }

    @Test
    void aMeteredCapabilityBeyondTheRemainingQuotaNeedsAnUpgrade() {
        TenantContext ctx = EntitlementFixtures.contextOf(fixtures.activeTenantWithQuota(5));

        assertThat(entitlements.decide(ctx, Capabilities.AI_SURVEY_GENERATE, 5)).isEqualTo(Decision.allow());
        assertThat(entitlements.decide(ctx, Capabilities.AI_SURVEY_GENERATE, 6))
                .isEqualTo(Decision.needUpgrade(DecisionReason.QUOTA_EXCEEDED));
    }

    @Test
    void aMeteredCapabilityWithoutAQuotaInThePlanNeedsAnUpgrade() {
        TenantContext ctx = EntitlementFixtures.contextOf(
                fixtures.activeTenant(Set.of(Capabilities.AI_SURVEY_GENERATE), Map.of()));

        assertThat(entitlements.decide(ctx, Capabilities.AI_SURVEY_GENERATE, 1))
                .isEqualTo(Decision.needUpgrade(DecisionReason.QUOTA_NOT_IN_PLAN));
    }

    @Test
    void perSurveyMetersRequireASurveySubject() {
        TenantContext ctx = EntitlementFixtures.contextOf(fixtures.activeTenantWithQuota(5));

        assertThatThrownBy(() -> entitlements.decide(ctx, Capabilities.RESPONSE_COLLECT, 1))
                .isInstanceOf(IllegalArgumentException.class);
        assertThat(entitlements.decide(ctx, Capabilities.RESPONSE_COLLECT, "survey-1", 5))
                .isEqualTo(Decision.allow());
    }

    @Test
    void nonPositiveQuantitiesAreRejected() {
        TenantContext ctx = EntitlementFixtures.contextOf(fixtures.activeTenantWithQuota(5));

        assertThatThrownBy(() -> entitlements.decide(ctx, Capabilities.SURVEY_WRITE, 0))
                .isInstanceOf(IllegalArgumentException.class);
    }

    private TenantId tenantExpiredDaysAgo(int daysAgo, int exportWindowDays) {
        TenantId tenant = TenantId.random();
        PlanVersion plan = fixtures.publishWithExportWindow(EntitlementFixtures.ALL_CAPABILITIES,
                Map.of(Meters.VALID_COMPLETED_RESPONSE, 100L, Meters.AI_TOKENS, 100L), exportWindowDays);
        subscriptions.start(tenant, plan.id(), SubscriptionKind.PAID,
                now.minus(Duration.ofDays(daysAgo + 365L)), now.minus(Duration.ofDays(daysAgo)));
        return tenant;
    }
}
