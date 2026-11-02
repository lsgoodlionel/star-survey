package cn.mjy.platform.onboarding;

import cn.mjy.platform.entitlement.PlanCatalog;
import cn.mjy.platform.entitlement.PlanDefinition;
import cn.mjy.platform.entitlement.PlanVersion;
import cn.mjy.platform.entitlement.SubscriptionKind;
import cn.mjy.platform.shared.TenantId;
import cn.mjy.platform.shared.security.CurrentTenant;
import cn.mjy.platform.tenant.PlatformOperatorGuard;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.UUID;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.ExceptionHandler;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.ResponseStatus;
import org.springframework.web.bind.annotation.RestController;

/** 平台运营的开通接口：发布套餐版本、为新租户开通订阅并初始化所有者。 */
@RestController
public class OnboardingController {

    private final PlatformOperatorGuard guard;
    private final PlanCatalog plans;
    private final OnboardingService onboarding;
    private final PlanPublishingService publishing;
    private final CurrentTenant currentTenant;
    private final boolean enforcePersonnel;

    public OnboardingController(PlatformOperatorGuard guard, PlanCatalog plans, OnboardingService onboarding,
                                PlanPublishingService publishing, CurrentTenant currentTenant,
                                @Value("${platform.admin.enforce-personnel:false}") boolean enforcePersonnel) {
        this.guard = guard;
        this.plans = plans;
        this.onboarding = onboarding;
        this.publishing = publishing;
        this.currentTenant = currentTenant;
        this.enforcePersonnel = enforcePersonnel;
    }

    @PostMapping("/v1/platform/plans")
    @ResponseStatus(HttpStatus.CREATED)
    public PlanView publishPlan(@RequestBody PublishPlan request) {
        guard.requireOperator();
        if (enforcePersonnel && request.expectedVersion() == null)
            throw new IllegalArgumentException("expected plan version is required");
        if (request.planCode() == null || request.capabilities() == null || request.quotas() == null
                || request.capabilities().stream().anyMatch(java.util.Objects::isNull)
                || request.quotas().entrySet().stream().anyMatch(e -> e.getKey() == null || e.getValue() == null))
            throw new IllegalArgumentException("invalid plan definition");
        PlanVersion plan = publishing.publish(new PlanDefinition(request.planCode(), Set.copyOf(request.capabilities()),
                Map.copyOf(request.quotas()), request.exportWindowDays()), request.expectedVersion(), currentTenant.require());
        return new PlanView(plan.id().toString(), plan.planCode(), plan.version());
    }

    @GetMapping("/v1/platform/plan-catalog/{planCode}/versions")
    public PlanHistory history(@PathVariable String planCode, @RequestParam(defaultValue = "0") int after,
                               @RequestParam(defaultValue = "50") int limit) {
        guard.requireOperator();
        if (!planCode.matches("[a-z][a-z0-9_.-]{1,63}") || after < 0 || limit < 1 || limit > 100)
            throw new IllegalArgumentException("invalid plan history cursor or limit");
        List<PlanVersion> rows = plans.versionsAfter(planCode, after, limit + 1);
        List<PlanDetail> items = rows.stream().limit(limit).map(PlanDetail::of).toList();
        return new PlanHistory(items, rows.size() > limit ? items.getLast().version() : null);
    }

    @GetMapping("/v1/platform/plans/{planCode}")
    public ResponseEntity<PlanView> latestPlan(@PathVariable String planCode) {
        guard.requireOperator();
        List<PlanVersion> versions = plans.versionsOf(planCode);
        if (versions.isEmpty()) {
            return ResponseEntity.notFound().build();
        }
        PlanVersion plan = versions.getLast();
        return ResponseEntity.ok(new PlanView(plan.id().toString(), plan.planCode(), plan.version()));
    }

    @GetMapping("/v1/platform/plan-catalog")
    public PlanDirectory catalog(@RequestParam(defaultValue = "") String after,
                                 @RequestParam(defaultValue = "50") int limit) {
        guard.requireOperator();
        if (limit < 1 || limit > 100 || after.length() > 64 || !after.matches("[a-z0-9_.-]*")) {
            throw new IllegalArgumentException("invalid plan catalog cursor or limit");
        }
        List<PlanVersion> rows = plans.latestAfter(after, limit + 1);
        List<PlanDetail> items = rows.stream().limit(limit).map(PlanDetail::of).toList();
        return new PlanDirectory(items, rows.size() > limit ? items.getLast().planCode() : null);
    }

    @GetMapping("/v1/platform/plan-catalog/definitions")
    public PlanDefinitions definitions() {
        guard.requireOperator();
        return new PlanDefinitions(plans.capabilityLabels(), plans.meterLabels());
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
                              int exportWindowDays, Integer expectedVersion) {
    }

    public record PlanView(String id, String planCode, int version) {
    }

    public record PlanDirectory(List<PlanDetail> items, String nextCursor) { }
    public record PlanDefinitions(List<PlanCatalog.DefinitionLabel> capabilities, List<PlanCatalog.DefinitionLabel> meters) { }
    public record PlanHistory(List<PlanDetail> items, Integer nextCursor) { }

    public record PlanDetail(String id, String planCode, int version, Set<String> capabilities,
                             Map<String, String> quotas, int exportWindowDays, java.time.Instant publishedAt) {
        static PlanDetail of(PlanVersion plan) {
            Map<String, String> quotas = plan.quotas().entrySet().stream().collect(
                    java.util.stream.Collectors.toUnmodifiableMap(Map.Entry::getKey, entry -> Long.toString(entry.getValue())));
            return new PlanDetail(plan.id().toString(), plan.planCode(), plan.version(), plan.capabilities(),
                    quotas, plan.exportWindowDays(), plan.publishedAt());
        }
    }

    public record Onboard(String planVersionId, String kind, int days, String ownerActorId) {
    }

    public record OnboardingView(String tenantId, String ownerActorId) {
    }
}
