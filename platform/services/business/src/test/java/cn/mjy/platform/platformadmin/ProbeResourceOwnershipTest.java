package cn.mjy.platform.platformadmin;

import static org.assertj.core.api.Assertions.assertThat;
import cn.mjy.platform.entitlement.PlanCatalog;
import cn.mjy.platform.entitlement.PlanDefinition;
import cn.mjy.platform.tenant.TenantService;
import cn.mjy.platform.tenant.engine.EngineInstanceService;
import java.util.Map;
import java.util.Set;
import java.util.UUID;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.jdbc.core.simple.JdbcClient;
import cn.mjy.platform.shared.tenant.TenantScope;

@SpringBootTest
class ProbeResourceOwnershipTest {
    @Autowired TenantService tenants;
    @Autowired EngineInstanceService engines;
    @Autowired PlanCatalog plans;
    @Autowired JdbcClient jdbc;
    @Autowired TenantScope scope;

    @Test void trustsOnlyTheInstallationRunAndResourcesRecordedByTheServer() {
        UUID installation = UUID.randomUUID(), run = UUID.randomUUID();
        String actor = "probe:" + installation + ":" + run;
        String code = "production-probe-" + run.toString().replace("-", "").substring(0, 12);
        var tenant = tenants.create(code, "Production probe", actor, "isolated");
        tenants.changeStatus(tenant.id(), cn.mjy.platform.tenant.TenantStatus.ACTIVE, actor, "isolated");
        var plan = plans.publish(new PlanDefinition(code, Set.of("survey.read", "survey.write", "response.collect", "response.export"), Map.of("member.seats", 1L, "response.valid_completed", 100L), 1));
        String engine = "probe-" + UUID.randomUUID();
        engines.register(tenant.id(), engine, "http://engine", actor, "isolated");
        var identity = new ProbeCredentialVerifier.Credential(installation, run, tenant.id().value(), engine, Set.of("probe.engine.secret.read"), java.time.Instant.now().plusSeconds(60));
        var ownership = new ProbeResourceOwnership(jdbc, scope, plans, engines, installation);
        assertThat(ownership.matches(identity, plan.id())).isTrue();
        assertThat(ownership.matches(new ProbeCredentialVerifier.Credential(UUID.randomUUID(), run, tenant.id().value(), engine, identity.scopes(), java.time.Instant.now().plusSeconds(60)), plan.id())).isFalse();
        assertThat(ownership.matches(new ProbeCredentialVerifier.Credential(installation, UUID.randomUUID(), tenant.id().value(), engine, identity.scopes(), java.time.Instant.now().plusSeconds(60)), plan.id())).isFalse();
        assertThat(ownership.matches(identity, UUID.randomUUID())).isFalse();
        assertThat(ownership.matches(new ProbeCredentialVerifier.Credential(installation, run, tenant.id().value(), "other", identity.scopes(), java.time.Instant.now().plusSeconds(60)), plan.id())).isFalse();
        var other = tenants.create("customer-" + UUID.randomUUID().toString().substring(0, 8), "Customer", "owner", "isolated");
        assertThat(ownership.matches(new ProbeCredentialVerifier.Credential(installation, run, other.id().value(), engine, identity.scopes(), java.time.Instant.now().plusSeconds(60)), plan.id())).isFalse();
        engines.changeStatus(tenant.id(), engine, cn.mjy.platform.tenant.engine.EngineInstanceStatus.DRAINING, actor, "isolated");
        assertThat(ownership.matches(identity, plan.id())).isFalse();
    }
}
