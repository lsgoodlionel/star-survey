package cn.mjy.platform.onboarding;

import cn.mjy.platform.access.MemberService;
import cn.mjy.platform.access.SeatLimitExceededException;
import cn.mjy.platform.audit.AuditLogRepository;
import cn.mjy.platform.entitlement.Meters;
import cn.mjy.platform.entitlement.PlanCatalog;
import cn.mjy.platform.entitlement.PlanVersion;
import cn.mjy.platform.entitlement.SubscriptionKind;
import cn.mjy.platform.entitlement.SubscriptionService;
import cn.mjy.platform.shared.TenantId;
import cn.mjy.platform.shared.tenant.TenantScope;
import cn.mjy.platform.tenant.Tenant;
import cn.mjy.platform.tenant.TenantService;
import cn.mjy.platform.tenant.TenantStatus;
import java.time.Duration;
import java.time.Instant;
import java.util.UUID;
import org.springframework.http.HttpStatus;
import org.springframework.stereotype.Service;

/**
 * 租户开通编排（跨租户、额度、权限三个模块，只调用它们的公开服务）。
 *
 * <p>所有者要占一个席位，而席位上限来自订阅，所以"开通订阅"与"初始化所有者"必须在同一事务里：
 * 任何一步失败都整体回滚，不会留下"有订阅却没有管理员"之类的半开通状态。
 * {@link TenantScope} 内的嵌套调用会加入外层事务。
 */
@Service
public class OnboardingService {

    static final String ACTION_ONBOARD = "tenant.onboard";

    private final TenantService tenants;
    private final PlanCatalog plans;
    private final SubscriptionService subscriptions;
    private final MemberService members;
    private final TenantScope tenantScope;
    private final AuditLogRepository audit;

    public OnboardingService(TenantService tenants, PlanCatalog plans, SubscriptionService subscriptions,
                             MemberService members, TenantScope tenantScope, AuditLogRepository audit) {
        this.tenants = tenants;
        this.plans = plans;
        this.subscriptions = subscriptions;
        this.members = members;
        this.tenantScope = tenantScope;
        this.audit = audit;
    }

    public void onboard(TenantId tenantId, OnboardingRequest request, String operator, String traceId) {
        Tenant tenant = tenants.find(tenantId)
                .orElseThrow(() -> new OnboardingException(HttpStatus.NOT_FOUND, "unknown_tenant", "no such tenant"));
        if (tenant.status() != TenantStatus.PROVISIONING) {
            throw new OnboardingException(HttpStatus.CONFLICT, "tenant_not_provisioning",
                    "only a tenant in provisioning can be onboarded");
        }
        PlanVersion plan = plans.find(request.planVersionId())
                .orElseThrow(() -> new OnboardingException(HttpStatus.NOT_FOUND, "unknown_plan", "no such plan version"));
        if (plan.quotaFor(Meters.MEMBER_SEATS).orElse(0) < 1) {
            throw noSeats();
        }
        tenantScope.run(tenantId, () -> startAndBootstrap(tenantId, request, operator, traceId));
    }

    private void startAndBootstrap(TenantId tenant, OnboardingRequest request, String operator, String traceId) {
        if (subscriptions.current(tenant).isPresent()) {
            throw alreadyOnboarded();
        }
        Instant now = Instant.now();
        subscriptions.start(tenant, request.planVersionId(), request.kind(), now,
                now.plus(Duration.ofDays(request.days())));
        try {
            members.bootstrapOwner(tenant, request.ownerActorId(), traceId);
        } catch (IllegalStateException alreadyHasMembers) {
            // 并发开通时另一方已初始化所有者：本事务整体回滚，连同刚开通的订阅。
            throw alreadyOnboarded();
        } catch (SeatLimitExceededException noSeat) {
            // 前置检查之外的兜底：席位不足同样整体回滚，不留下没有管理员的订阅。
            throw noSeats();
        }
        audit.record(tenant, operator, ACTION_ONBOARD, "tenant/" + tenant + " owner=" + request.ownerActorId(), traceId);
    }

    private static OnboardingException noSeats() {
        return new OnboardingException(HttpStatus.UNPROCESSABLE_ENTITY, "plan_has_no_seats",
                "the plan grants no member seat, so the owner cannot be created");
    }

    private static OnboardingException alreadyOnboarded() {
        return new OnboardingException(HttpStatus.CONFLICT, "already_onboarded", "tenant is already onboarded");
    }

    /** 开通参数；天数限定在 1–3650。 */
    public record OnboardingRequest(UUID planVersionId, SubscriptionKind kind, int days, String ownerActorId) {

        public OnboardingRequest {
            if (planVersionId == null || kind == null || ownerActorId == null || ownerActorId.isBlank()) {
                throw new IllegalArgumentException("planVersionId, kind and ownerActorId are required");
            }
            if (kind == SubscriptionKind.CANCELLED) {
                throw new IllegalArgumentException("onboarding starts a trial or paid subscription");
            }
            if (days < 1 || days > 3650) {
                throw new IllegalArgumentException("days must be between 1 and 3650");
            }
        }
    }
}
