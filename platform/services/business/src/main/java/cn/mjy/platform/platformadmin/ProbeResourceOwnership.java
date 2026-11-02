package cn.mjy.platform.platformadmin;

import cn.mjy.platform.entitlement.PlanCatalog;
import cn.mjy.platform.shared.TenantId;
import cn.mjy.platform.shared.tenant.TenantScope;
import cn.mjy.platform.tenant.engine.EngineInstanceService;
import cn.mjy.platform.tenant.engine.EngineInstanceStatus;
import java.util.Map;
import java.util.Set;
import java.util.UUID;
import org.springframework.jdbc.core.simple.JdbcClient;

/** Server-side provenance check; caller still checks the independently verified token and request scope. */
final class ProbeResourceOwnership {
    private final JdbcClient jdbc;
    private final TenantScope scope;
    private final PlanCatalog plans;
    private final EngineInstanceService engines;
    private final UUID installation;
    ProbeResourceOwnership(JdbcClient jdbc, TenantScope scope, PlanCatalog plans, EngineInstanceService engines, UUID installation) {
        this.jdbc = jdbc; this.scope = scope; this.plans = plans; this.engines = engines; this.installation = installation;
    }
    boolean matches(ProbeCredentialVerifier.Credential credential, UUID planId) {
        if (credential == null || !installation.equals(credential.installation()) || credential.tenant() == null
                || credential.engine() == null || planId == null) return false;
        String code = "production-probe-" + credential.run().toString().replace("-", "").substring(0, 12);
        String actor = "probe:" + installation + ":" + credential.run();
        var tenant = new TenantId(credential.tenant());
        boolean tenantOwned = jdbc.sql("SELECT EXISTS(SELECT 1 FROM tenant WHERE id=:id AND code=:code AND status='active')")
                .param("id", credential.tenant()).param("code", code).query(Boolean.class).single()
                && audited(tenant, actor, "tenant.create", "tenant/" + credential.tenant());
        if (!tenantOwned) return false;
        boolean planOwned = plans.find(planId).filter(plan -> plan.planCode().equals(code) && plan.version() == 1
                && plan.capabilities().equals(Set.of("survey.read", "survey.write", "response.collect", "response.export"))
                && plan.quotas().equals(Map.of("member.seats", 1L, "response.valid_completed", 100L))
                && plan.exportWindowDays() == 1).isPresent();
        return planOwned && engines.find(tenant, credential.engine()).filter(instance -> instance.status() == EngineInstanceStatus.ACTIVE).isPresent()
                && audited(tenant, actor, "engine_instance.register", "engine_instance/" + credential.engine());
    }
    private boolean audited(TenantId tenant, String actor, String action, String resource) {
        return scope.call(tenant, () -> jdbc.sql("SELECT EXISTS(SELECT 1 FROM audit_log WHERE actor_id=:actor AND action=:action AND resource=:resource)")
                .param("actor", actor).param("action", action).param("resource", resource).query(Boolean.class).single());
    }
}
