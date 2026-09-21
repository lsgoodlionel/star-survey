package cn.mjy.platform.entitlement;

import java.time.Instant;
import java.util.Optional;
import java.util.OptionalLong;
import org.springframework.stereotype.Component;

/**
 * 判定规则本身。调用方必须已处于 {@link cn.mjy.platform.shared.tenant.TenantScope} 内，
 * 这样判定与随后的预占落在同一事务、看到同一租户的数据。
 *
 * 顺序：能力是否登记 -> 是否有订阅 -> 套餐是否开启 -> 只读限制 -> 额度。
 */
@Component
class EntitlementEvaluator {

    private final PlanCatalog catalog;
    private final SubscriptionService subscriptions;
    private final UsageCounters counters;

    EntitlementEvaluator(PlanCatalog catalog, SubscriptionService subscriptions, UsageCounters counters) {
        this.catalog = catalog;
        this.subscriptions = subscriptions;
        this.counters = counters;
    }

    Evaluation evaluate(String capabilityCode, String subject, long quantity, Instant now) {
        if (quantity <= 0) {
            throw new IllegalArgumentException("quantity must be positive, got " + quantity);
        }
        Optional<CapabilityDefinition> capability = catalog.capability(capabilityCode);
        if (capability.isEmpty()) {
            return Evaluation.rejected(Decision.deny(DecisionReason.UNKNOWN_CAPABILITY));
        }
        Optional<Subscription> subscription = subscriptions.currentInScope(now);
        if (subscription.isEmpty()) {
            return Evaluation.rejected(Decision.deny(DecisionReason.NO_SUBSCRIPTION));
        }
        PlanVersion plan = catalog.find(subscription.get().planVersionId()).orElseThrow();
        Decision access = accessDecision(capability.get(), subscription.get(), plan, now);
        if (!access.isAllowed() || !capability.get().isMetered()) {
            return new Evaluation(access, capability.get(), subscription.get(), null, 0);
        }
        return meteredDecision(capability.get(), subscription.get(), plan, subject, quantity);
    }

    private static Decision accessDecision(CapabilityDefinition capability, Subscription subscription,
                                           PlanVersion plan, Instant now) {
        if (!plan.enables(capability.code())) {
            return Decision.needUpgrade(DecisionReason.CAPABILITY_NOT_IN_PLAN);
        }
        if (!subscription.statusAt(now).isReadOnly()) {
            return Decision.allow();
        }
        return switch (capability.access()) {
            case READ -> Decision.allow();
            case EXPORT -> now.isBefore(subscription.exportWindowEndsAt(plan.exportWindowDays()))
                    ? Decision.allow()
                    : Decision.deny(DecisionReason.EXPORT_WINDOW_CLOSED);
            case WRITE -> Decision.deny(DecisionReason.SUBSCRIPTION_READ_ONLY);
        };
    }

    private Evaluation meteredDecision(CapabilityDefinition capability, Subscription subscription,
                                       PlanVersion plan, String subject, long quantity) {
        MeterDefinition meter = catalog.meter(capability.meter()).orElseThrow();
        CounterKey key = CounterKey.of(meter, subject, subscription);
        OptionalLong limit = plan.quotaFor(meter.code());
        if (limit.isEmpty()) {
            return new Evaluation(Decision.needUpgrade(DecisionReason.QUOTA_NOT_IN_PLAN),
                    capability, subscription, key, 0);
        }
        long used = counters.used(key);
        Decision decision = used + quantity <= limit.getAsLong()
                ? Decision.allow()
                : Decision.needUpgrade(DecisionReason.QUOTA_EXCEEDED);
        return new Evaluation(decision, capability, subscription, key, limit.getAsLong());
    }

    /**
     * 判定结果与判定时解析出的上下文。放行且计量时 {@code counterKey} 与 {@code limit} 有效，
     * 预占据此做原子的条件更新（判定时读到的余额只是参考，不作为并发下的依据）。
     */
    record Evaluation(Decision decision, CapabilityDefinition capability, Subscription subscription,
                      CounterKey counterKey, long limit) {

        static Evaluation rejected(Decision decision) {
            return new Evaluation(decision, null, null, null, 0);
        }
    }
}
