package cn.mjy.platform.entitlement;

import cn.mjy.platform.shared.TenantContext;
import cn.mjy.platform.shared.TenantId;
import java.time.Duration;
import java.time.Instant;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.UUID;
import org.springframework.stereotype.Component;

/** 测试夹具：发布套餐、开通订阅、构造可信上下文。套餐代码随机，避免测试之间互相影响。 */
@Component
class EntitlementFixtures {

    static final Set<String> ALL_CAPABILITIES = Set.of(
            Capabilities.SURVEY_READ, Capabilities.SURVEY_WRITE, Capabilities.RESPONSE_COLLECT,
            Capabilities.RESPONSE_EXPORT, Capabilities.AI_SURVEY_GENERATE, Capabilities.AI_RESPONSE_ANALYZE);

    private final PlanCatalog plans;
    private final SubscriptionService subscriptions;

    EntitlementFixtures(PlanCatalog plans, SubscriptionService subscriptions) {
        this.plans = plans;
        this.subscriptions = subscriptions;
    }

    static String uniquePlanCode() {
        return "plan_" + UUID.randomUUID().toString().replace("-", "").substring(0, 12);
    }

    static TenantContext contextOf(TenantId tenant) {
        return new TenantContext(tenant, "actor-" + tenant, List.of("owner"), "trace-" + UUID.randomUUID());
    }

    PlanVersion publish(Set<String> capabilities, Map<String, Long> quotas) {
        return publishWithExportWindow(capabilities, quotas, 30);
    }

    PlanVersion publishWithExportWindow(Set<String> capabilities, Map<String, Long> quotas, int exportWindowDays) {
        return plans.publish(new PlanDefinition(uniquePlanCode(), capabilities, quotas, exportWindowDays));
    }

    /** 开通一个从一天前开始、一年后到期的正式订阅。 */
    TenantId activeTenant(Set<String> capabilities, Map<String, Long> quotas) {
        TenantId tenant = TenantId.random();
        PlanVersion plan = publish(capabilities, quotas);
        Instant now = Instant.now();
        subscriptions.start(tenant, plan.id(), SubscriptionKind.PAID,
                now.minus(Duration.ofDays(1)), now.plus(Duration.ofDays(365)));
        return tenant;
    }

    /** 全部能力开启、各计量额度相同的正式订阅。 */
    TenantId activeTenantWithQuota(long quota) {
        return activeTenant(ALL_CAPABILITIES, Map.of(
                Meters.VALID_COMPLETED_RESPONSE, quota,
                Meters.AI_TOKENS, quota));
    }
}
