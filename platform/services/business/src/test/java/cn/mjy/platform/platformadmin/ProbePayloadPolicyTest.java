package cn.mjy.platform.platformadmin;

import static org.assertj.core.api.Assertions.assertThat;
import java.util.List;
import java.util.Map;
import java.util.UUID;
import org.junit.jupiter.api.Test;

class ProbePayloadPolicyTest {
    @Test void onlyTheBoundProbeResourcesAndExactProvisioningPayloadAreAccepted() {
        UUID probe = UUID.randomUUID(), plan = UUID.randomUUID();
        String code = "production-probe-" + probe.toString().replace("-", "").substring(0, 12);
        var tenant = Map.<String, Object>of("code", code, "name", "Production probe");
        var policy = new ProbePayloadPolicy(probe, plan, "production-engine-01");
        assertThat(policy.allows("probe.tenant.create", tenant)).isTrue();
        assertThat(policy.allows("probe.tenant.create", Map.of("code", "customer", "name", "Production probe"))).isFalse();
        assertThat(policy.allows("probe.tenant.create", Map.of("code", code, "name", "Production probe", "extra", true))).isFalse();
        assertThat(policy.allows("probe.plan.create", Map.of("planCode", code, "capabilities", List.of("survey.read", "survey.write", "response.collect", "response.export"), "quotas", Map.of("member.seats", 1, "response.valid_completed", 100), "exportWindowDays", 1))).isTrue();
        assertThat(policy.allows("probe.plan.create", Map.of("planCode", code, "capabilities", List.of("survey.read", "survey.write", "response.collect", "response.export"), "quotas", Map.of("member.seats", 1000, "response.valid_completed", 100), "exportWindowDays", 1))).isFalse();
        assertThat(policy.allows("probe.onboard", Map.of("planVersionId", plan.toString(), "kind", "TRIAL", "days", 3650, "ownerActorId", "production-probe-owner"))).isTrue();
        assertThat(policy.allows("probe.onboard", Map.of("planVersionId", UUID.randomUUID().toString(), "kind", "TRIAL", "days", 3650, "ownerActorId", "production-probe-owner"))).isFalse();
        assertThat(policy.allows("probe.tenant.activate", Map.of("status", "active"))).isTrue();
        assertThat(policy.allows("probe.tenant.activate", Map.of("status", "closed"))).isFalse();
        assertThat(policy.allows("probe.engine.register", Map.of("id", "production-engine-01", "baseUrl", "http://engine"))).isTrue();
        assertThat(policy.allows("probe.engine.register", Map.of("id", "other", "baseUrl", "http://engine"))).isFalse();
        assertThat(policy.allows("probe.engine.secret.read", Map.of())).isTrue();
        assertThat(policy.allows("probe.engine.secret.read", Map.of("extra", true))).isFalse();
    }
}
