package cn.mjy.platform.platformadmin;

import cn.mjy.platform.audit.AuditLogRepository;
import cn.mjy.platform.engine.EngineEventKeys;
import cn.mjy.platform.entitlement.PlanCatalog;
import cn.mjy.platform.entitlement.PlanDefinition;
import cn.mjy.platform.entitlement.PlanVersion;
import cn.mjy.platform.entitlement.SubscriptionKind;
import cn.mjy.platform.entitlement.SubscriptionService;
import cn.mjy.platform.onboarding.OnboardingService;
import cn.mjy.platform.shared.TenantId;
import cn.mjy.platform.shared.tenant.TenantScope;
import cn.mjy.platform.tenant.TenantService;
import cn.mjy.platform.tenant.TenantStatus;
import cn.mjy.platform.tenant.engine.EngineInstanceService;
import cn.mjy.platform.tenant.engine.EngineInstanceStatus;
import java.util.Map;
import java.util.Set;
import java.util.UUID;
import java.time.Instant;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

/** One fixed, idempotent probe setup. No request-supplied tenant, plan, engine or owner. */
@Service
class ProbeSetupService {
    private static final String OWNER = "production-probe-owner";
    private static final String ENGINE = "production-engine-01";
    private static final Set<String> CAPABILITIES = Set.of("survey.read", "survey.write", "response.collect", "response.export");
    private static final Map<String, Long> QUOTAS = Map.of("member.seats", 1L, "response.valid_completed", 100L);
    private final JdbcClient jdbc; private final TenantScope scope; private final TenantService tenants;
    private final PlanCatalog plans; private final SubscriptionService subscriptions; private final OnboardingService onboarding;
    private final EngineInstanceService engines; private final EngineEventKeys keys; private final AuditLogRepository audit;
    ProbeSetupService(JdbcClient jdbc, TenantScope scope, TenantService tenants, PlanCatalog plans,
                      SubscriptionService subscriptions, OnboardingService onboarding, EngineInstanceService engines,
                      EngineEventKeys keys, AuditLogRepository audit) {
        this.jdbc = jdbc; this.scope = scope; this.tenants = tenants; this.plans = plans; this.subscriptions = subscriptions;
        this.onboarding = onboarding; this.engines = engines; this.keys = keys; this.audit = audit;
    }

    @Transactional
    Result setup(ProbeCredentialVerifier.Credential credential) {
        String actor = "probe:" + credential.installation() + ":" + credential.run();
        String code = "production-probe-" + credential.run().toString().replace("-", "").substring(0, 12);
        String trace = UUID.randomUUID().toString();
        jdbc.sql("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))").param("key", actor).query((rs, n) -> true).single();
        if (credential.expiresAt() == null || !credential.expiresAt().isAfter(Instant.now()))
            throw new IllegalStateException("probe credential expired");
        if (!keys.isConfigured()) throw new IllegalStateException("engine event key unavailable");
        var tenant = tenants.findByCode(code).orElseGet(() -> tenants.create(code, "Production probe", actor, trace));
        TenantId id = tenant.id();
        if (!audited(id, actor, "tenant.create", "tenant/" + id)) throw new IllegalStateException("probe tenant ownership mismatch");
        var versions = plans.versionsOf(code);
        PlanVersion plan;
        if (versions.isEmpty()) {
            plan = plans.publish(new PlanDefinition(code, CAPABILITIES, QUOTAS, 1));
            scope.run(id, () -> audit.record(id, actor, "probe.plan.created", "plan/" + plan.id(), trace));
        } else {
            if (versions.size() != 1) throw new IllegalStateException("probe plan version mismatch");
            plan = versions.getFirst();
            if (!plan.capabilities().equals(CAPABILITIES) || !plan.quotas().equals(QUOTAS) || plan.exportWindowDays() != 1
                    || !audited(id, actor, "probe.plan.created", "plan/" + plan.id()))
                throw new IllegalStateException("probe plan ownership mismatch");
        }
        var current = subscriptions.current(id);
        if (current.isEmpty()) {
            if (tenant.status() != TenantStatus.PROVISIONING) throw new IllegalStateException("probe tenant state mismatch");
            onboarding.onboard(id, new OnboardingService.OnboardingRequest(plan.id(), SubscriptionKind.TRIAL, 3650, OWNER), actor, trace);
        } else if (!current.get().planVersionId().equals(plan.id()) || current.get().kind() != SubscriptionKind.TRIAL
                || current.get().statusAt(Instant.now()).isReadOnly()
                || !ownerActive(id)) throw new IllegalStateException("probe subscription mismatch");
        if (tenant.status() == TenantStatus.PROVISIONING) tenants.changeStatus(id, TenantStatus.ACTIVE, actor, trace);
        else if (tenant.status() != TenantStatus.ACTIVE) throw new IllegalStateException("probe tenant not active");
        var existing = engines.find(id, ENGINE);
        if (existing.isEmpty()) engines.register(id, ENGINE, "http://engine", actor, trace);
        else if (existing.get().status() != EngineInstanceStatus.ACTIVE || !existing.get().baseUrl().equals("http://engine")
                || !audited(id, actor, "engine_instance.register", "engine_instance/" + ENGINE))
            throw new IllegalStateException("probe engine ownership mismatch");
        scope.run(id, () -> audit.record(id, actor, "engine_instance.event_secret_issue", "engine_instance/" + ENGINE, trace));
        return new Result(id.toString(), OWNER, keys.derivedSecret(ENGINE));
    }
    private boolean audited(TenantId id, String actor, String action, String resource) {
        return scope.call(id, () -> jdbc.sql("SELECT EXISTS(SELECT 1 FROM audit_log WHERE actor_id=:actor AND action=:action AND resource=:resource)")
                .param("actor", actor).param("action", action).param("resource", resource).query(Boolean.class).single());
    }
    private boolean ownerActive(TenantId id) {
        return scope.call(id, () -> jdbc.sql("SELECT EXISTS(SELECT 1 FROM access_member m JOIN access_grant g ON g.tenant_id=m.tenant_id AND g.actor_id=m.actor_id WHERE m.actor_id=:actor AND m.status='active' AND g.role_code='tenant_owner' AND g.resource_id IS NULL AND g.expires_at IS NULL)")
                .param("actor", OWNER).query(Boolean.class).single());
    }
    record Result(String tenantId, String actorId, String engineSecret) { }
}
