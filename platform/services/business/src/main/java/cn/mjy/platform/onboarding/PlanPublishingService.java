package cn.mjy.platform.onboarding;

import cn.mjy.platform.entitlement.PlanCatalog;
import cn.mjy.platform.entitlement.PlanDefinition;
import cn.mjy.platform.entitlement.PlanVersion;
import cn.mjy.platform.shared.TenantContext;
import java.util.UUID;
import org.springframework.http.HttpStatus;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

/** Serialize publication for a plan and commit the immutable version with its audit. */
@Service
public class PlanPublishingService {
    private final JdbcClient jdbc;
    private final PlanCatalog plans;

    public PlanPublishingService(JdbcClient jdbc, PlanCatalog plans) { this.jdbc = jdbc; this.plans = plans; }

    @Transactional
    public PlanVersion publish(PlanDefinition definition, Integer expectedVersion, TenantContext actor) {
        if (expectedVersion != null && expectedVersion < 0) throw new IllegalArgumentException("invalid expected version");
        jdbc.sql("SELECT pg_advisory_xact_lock(1979110202, hashtext(:code))").param("code", definition.planCode()).query().singleRow();
        int latest = jdbc.sql("SELECT COALESCE(MAX(version), 0) FROM plan_version WHERE plan_code=:code")
                .param("code", definition.planCode()).query(Integer.class).single();
        if (expectedVersion != null && latest != expectedVersion)
            throw new OnboardingException(HttpStatus.CONFLICT, "plan_version_conflict", "plan version changed, reload before publishing");
        PlanVersion plan = plans.publish(definition);
        jdbc.sql("""
                INSERT INTO platform_plan_audit(actor_tenant, actor_subject, plan_id, plan_code, plan_version, trace_id)
                VALUES (:tenant, :subject, :id, :code, :version, :trace)
                """).param("tenant", actor.tenantId().value()).param("subject", actor.actorId())
                .param("id", plan.id()).param("code", plan.planCode()).param("version", plan.version())
                .param("trace", UUID.randomUUID().toString()).update();
        return plan;
    }
}
