package cn.mjy.platform.onboarding;

import cn.mjy.platform.entitlement.PlanCatalog;
import cn.mjy.platform.entitlement.PlanDefinition;
import cn.mjy.platform.entitlement.PlanVersion;
import cn.mjy.platform.entitlement.SubscriptionKind;
import cn.mjy.platform.shared.TenantId;
import cn.mjy.platform.tenant.PlatformOperatorGuard;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.UUID;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.ExceptionHandler;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.ResponseStatus;
import org.springframework.web.bind.annotation.RestController;

/** 平台运营的开通接口：发布套餐版本、为新租户开通订阅并初始化所有者。 */
@RestController
public class OnboardingController {

    private final PlatformOperatorGuard guard;
    private final PlanCatalog plans;
    private final OnboardingService onboarding;

    public OnboardingController(PlatformOperatorGuard guard, PlanCatalog plans, OnboardingService onboarding) {
        this.guard = guard;
        this.plans = plans;
        this.onboarding = onboarding;
    }

    @PostMapping("/v1/platform/plans")
    @ResponseStatus(HttpStatus.CREATED)
    public PlanView publishPlan(@RequestBody PublishPlan request) {
        guard.requireOperator();
        PlanVersion plan = plans.publish(new PlanDefinition(request.planCode(), Set.copyOf(request.capabilities()),
                Map.copyOf(request.quotas()), request.exportWindowDays()));
        return new PlanView(plan.id().toString(), plan.planCode(), plan.version());
    }

    @PostMapping("/v1/platform/tenants/{tenantId}/onboarding")
    public OnboardingView onboard(@PathVariable String tenantId, @RequestBody Onboard request) {
        String operator = guard.requireOperator();
        var onboardingRequest = new OnboardingService.OnboardingRequest(
                UUID.fromString(request.planVersionId()), SubscriptionKind.valueOf(request.kind()),
                request.days(), request.ownerActorId());
        onboarding.onboard(TenantId.of(tenantId), onboardingRequest, operator, UUID.randomUUID().toString());
        return new OnboardingView(tenantId, request.ownerActorId());
    }

    @ExceptionHandler(OnboardingException.class)
    ResponseEntity<Map<String, String>> rejected(OnboardingException e) {
        return ResponseEntity.status(e.status()).body(Map.of("error", e.code()));
    }

    @ExceptionHandler(IllegalArgumentException.class)
    ResponseEntity<Map<String, String>> invalid(IllegalArgumentException e) {
        return ResponseEntity.badRequest().body(Map.of("error", "invalid_request"));
    }

    public record PublishPlan(String planCode, List<String> capabilities, Map<String, Long> quotas,
                              int exportWindowDays) {
    }

    public record PlanView(String id, String planCode, int version) {
    }

    public record Onboard(String planVersionId, String kind, int days, String ownerActorId) {
    }

    public record OnboardingView(String tenantId, String ownerActorId) {
    }
}
