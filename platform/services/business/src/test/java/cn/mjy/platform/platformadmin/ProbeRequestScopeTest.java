package cn.mjy.platform.platformadmin;

import static org.assertj.core.api.Assertions.assertThat;
import java.util.Set;
import java.util.UUID;
import org.junit.jupiter.api.Test;

class ProbeRequestScopeTest {
    @Test void permitsOnlyExactScopedMethodsAndTheBoundTenantAndEngine() {
        UUID tenant = UUID.randomUUID(), other = UUID.randomUUID();
        var all = Set.of("probe.tenant.create", "probe.plan.create", "probe.onboard", "probe.tenant.activate", "probe.engine.register", "probe.engine.secret.read");
        var credential = new ProbeCredentialVerifier.Credential(UUID.randomUUID(), UUID.randomUUID(), tenant, "engine-01", all);
        assertThat(ProbeRequestScope.allows(credential, "POST", "/v1/platform/tenants")).isTrue();
        assertThat(ProbeRequestScope.allows(credential, "POST", "/v1/platform/plans")).isTrue();
        assertThat(ProbeRequestScope.allows(credential, "POST", "/v1/platform/tenants/" + tenant + "/onboarding")).isTrue();
        assertThat(ProbeRequestScope.allows(credential, "POST", "/v1/platform/tenants/" + tenant + "/status")).isTrue();
        assertThat(ProbeRequestScope.allows(credential, "POST", "/v1/platform/tenants/" + tenant + "/engine-instances")).isTrue();
        assertThat(ProbeRequestScope.allows(credential, "POST", "/v1/platform/engine-instances/engine-01/event-secret")).isTrue();
        assertThat(ProbeRequestScope.allows(credential, "POST", "/v1/platform/tenants/" + other + "/onboarding")).isFalse();
        assertThat(ProbeRequestScope.allows(credential, "POST", "/v1/platform/engine-instances/other/event-secret")).isFalse();
        assertThat(ProbeRequestScope.allows(credential, "GET", "/v1/platform/tenants")).isFalse();
        assertThat(ProbeRequestScope.allows(new ProbeCredentialVerifier.Credential(UUID.randomUUID(), UUID.randomUUID(), tenant, "engine-01", Set.of("probe.tenant.create")), "POST", "/v1/platform/plans")).isFalse();
        assertThat(ProbeRequestScope.allows(credential, "POST", "/v1/platform-admin/grants/change")).isFalse();
    }
}
