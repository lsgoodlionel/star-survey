package cn.mjy.platform.entitlement;

import cn.mjy.platform.access.AccessDecisionService;
import cn.mjy.platform.access.Permission;
import cn.mjy.platform.shared.security.CurrentTenant;
import java.time.Instant;
import java.util.Map;
import java.util.Set;
import java.util.UUID;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RestController;

/** 当前租户的只读订阅；目标租户只能从已验证的会话获取。 */
@RestController
class SubscriptionController {
    private final CurrentTenant currentTenant;
    private final AccessDecisionService access;
    private final SubscriptionService subscriptions;
    private final PlanCatalog plans;

    SubscriptionController(CurrentTenant currentTenant, AccessDecisionService access,
                           SubscriptionService subscriptions, PlanCatalog plans) {
        this.currentTenant = currentTenant;
        this.access = access;
        this.subscriptions = subscriptions;
        this.plans = plans;
    }

    @GetMapping("/v1/subscription")
    ResponseEntity<SubscriptionView> current() {
        var context = currentTenant.require();
        access.require(context, Permission.MANAGE_SETTINGS, null);
        return subscriptions.current(context.tenantId()).map(subscription -> {
            var plan = plans.find(subscription.planVersionId())
                    .orElseThrow(() -> new IllegalStateException("subscription plan version missing"));
            return ResponseEntity.ok(new SubscriptionView(plan.id(), plan.planCode(), plan.version(),
                    subscription.statusAt(Instant.now()), subscription.startsAt(), subscription.endsAt(),
                    plan.capabilities(), plan.quotas(), plan.exportWindowDays(), plan.quotas().entrySet().stream()
                    .collect(java.util.stream.Collectors.toUnmodifiableMap(Map.Entry::getKey, e -> Long.toString(e.getValue())))));
        }).orElseGet(() -> ResponseEntity.notFound().build());
    }

    record SubscriptionView(UUID planVersionId, String planCode, int planVersion, SubscriptionStatus status,
                            Instant startsAt, Instant endsAt, Set<String> capabilities, Map<String, Long> quotas,
                            int exportWindowDays, Map<String, String> quotasExact) { }
}
