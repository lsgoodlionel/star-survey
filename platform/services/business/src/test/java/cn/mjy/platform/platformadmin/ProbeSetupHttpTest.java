package cn.mjy.platform.platformadmin;

import static org.assertj.core.api.Assertions.assertThat;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;
import com.jayway.jsonpath.JsonPath;
import com.nimbusds.jose.jwk.source.ImmutableSecret;
import java.nio.charset.StandardCharsets;
import java.time.Instant;
import java.util.List;
import java.util.UUID;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.Executors;
import java.util.concurrent.TimeUnit;
import javax.crypto.spec.SecretKeySpec;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.webmvc.test.autoconfigure.AutoConfigureMockMvc;
import org.springframework.security.oauth2.jose.jws.MacAlgorithm;
import org.springframework.security.oauth2.jwt.JwsHeader;
import org.springframework.security.oauth2.jwt.JwtClaimsSet;
import org.springframework.security.oauth2.jwt.JwtEncoderParameters;
import org.springframework.security.oauth2.jwt.NimbusJwtEncoder;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.mock.web.MockHttpServletRequest;
import cn.mjy.platform.tenant.TenantService;
import cn.mjy.platform.entitlement.PlanCatalog;
import cn.mjy.platform.entitlement.PlanDefinition;
import cn.mjy.platform.entitlement.SubscriptionService;
import cn.mjy.platform.entitlement.SubscriptionKind;
import cn.mjy.platform.shared.TenantId;
import java.util.Map;
import java.util.Set;
import cn.mjy.platform.tenant.TenantFixtures;
import java.sql.DriverManager;
import org.springframework.beans.factory.annotation.Value;

@SpringBootTest(properties = {"platform.admin.enforce-personnel=true", "PLATFORM_PROBE_HMAC_SECRET=isolated-probe-key-for-tests-only-000000000000", "PLATFORM_PROBE_INSTALLATION_ID=10000000-0000-4000-8000-000000000001", "PLATFORM_ENGINE_EVENTS_SECRET=isolated-events-key-for-tests-only-00000000000"})
@AutoConfigureMockMvc
class ProbeSetupHttpTest {
    private static final String KEY = "isolated-probe-key-for-tests-only-000000000000";
    private static final String INSTALL = "10000000-0000-4000-8000-000000000001";
    @Autowired MockMvc mvc;
    @Autowired TenantFixtures fixtures;
    @Autowired TenantService tenants;
    @Autowired PlanCatalog plans;
    @Autowired SubscriptionService subscriptions;
    @Value("${spring.flyway.url}") String dbUrl;
    @Value("${spring.flyway.user}") String dbOwner;
    @Value("${spring.flyway.password}") String dbPassword;

    @Test void sharedPersonnelOrEngineMasterKeyDisablesTheMachineEndpoint() throws Exception {
        var service = org.mockito.Mockito.mock(ProbeSetupService.class);
        var request = new MockHttpServletRequest();
        assertThat(new ProbeSetupController(service, KEY, INSTALL, KEY, "independent-engine-key").setup(request).getStatusCode().value()).isEqualTo(503);
        assertThat(new ProbeSetupController(service, KEY, INSTALL, "independent-personnel-key", KEY).setup(request).getStatusCode().value()).isEqualTo(503);
    }

    @Test void dedicatedMachineCredentialCreatesOnlyItsOwnProbeAndRetriesIdempotently() throws Exception {
        UUID run = UUID.randomUUID();
        String token = token(KEY, INSTALL, run, "survey-setup-probe", "probe.setup");
        mvc.perform(post("/v1/setup-probe")).andExpect(status().isUnauthorized());
        mvc.perform(post("/v1/setup-probe").header("Authorization", "Bearer " + token(KEY, UUID.randomUUID().toString(), run, "survey-setup-probe", "probe.setup"))).andExpect(status().isForbidden());
        mvc.perform(post("/v1/setup-probe").header("Authorization", "Bearer " + token(KEY, INSTALL, run, "wrong", "probe.setup"))).andExpect(status().isForbidden());
        mvc.perform(post("/v1/setup-probe").header("Authorization", "Bearer " + token("different-key-for-tests-only-00000000000000", INSTALL, run, "survey-setup-probe", "probe.setup"))).andExpect(status().isForbidden());
        mvc.perform(post("/v1/platform-admin/grants/change").header("Authorization", "Bearer " + token).contentType("application/json").content("{}"))
                .andExpect(status().isUnauthorized());
        mvc.perform(post("/v1/platform/tenants").header("Authorization", "Bearer " + token).contentType("application/json").content("{}"))
                .andExpect(status().isUnauthorized());
        mvc.perform(post("/v1/platform/tenants").header("Authorization", fixtures.operatorBearer()).contentType("application/json").content("{}"))
                .andExpect(status().isForbidden());
        mvc.perform(post("/v1/setup-probe").header("Authorization", "Bearer " + token(KEY, INSTALL, run, "survey-setup-probe", "probe.tenant.create")))
                .andExpect(status().isForbidden());
        mvc.perform(post("/v1/setup-probe").header("Authorization", "Bearer " + token).contentType("application/json").content("{\"tenantId\":\"forged\"}"))
                .andExpect(status().isBadRequest());
        UUID impostor = UUID.randomUUID();
        String impostorCode = "production-probe-" + impostor.toString().replace("-", "").substring(0, 12);
        tenants.create(impostorCode, "Production probe", "probe:" + INSTALL + ":" + impostor, "isolated-test");
        plans.publish(new PlanDefinition(impostorCode, Set.of("survey.read", "survey.write", "response.collect", "response.export"), Map.of("member.seats", 1L, "response.valid_completed", 100L), 1));
        mvc.perform(post("/v1/setup-probe").header("Authorization", "Bearer " + token(KEY, INSTALL, impostor, "survey-setup-probe", "probe.setup")))
                .andExpect(status().isConflict());
        var start = new CountDownLatch(1);
        try (var pool = Executors.newFixedThreadPool(2)) {
        var attemptA = pool.submit(() -> { start.await(); return mvc.perform(post("/v1/setup-probe").header("Authorization", "Bearer " + token)).andExpect(status().isOk()).andReturn().getResponse(); });
        var attemptB = pool.submit(() -> { start.await(); return mvc.perform(post("/v1/setup-probe").header("Authorization", "Bearer " + token)).andExpect(status().isOk()).andReturn().getResponse(); });
        start.countDown();
        var first = attemptA.get(30, TimeUnit.SECONDS);
        var second = attemptB.get(30, TimeUnit.SECONDS);
        assertThat((String) JsonPath.read(first.getContentAsString(), "$.tenantId")).isEqualTo((String) JsonPath.read(second.getContentAsString(), "$.tenantId"));
        assertThat((String) JsonPath.read(first.getContentAsString(), "$.actorId")).isEqualTo("production-probe-owner");
        assertThat(first.getHeader("Cache-Control")).contains("no-store");
        UUID collision = UUID.randomUUID();
        String collisionCode = "production-probe-" + collision.toString().replace("-", "").substring(0, 12);
        mvc.perform(post("/v1/setup-probe").header("Authorization", "Bearer " + token(KEY, INSTALL, collision, "survey-setup-probe", "probe.setup")))
                .andExpect(status().isConflict());
        assertThat(tenants.findByCode(collisionCode)).isEmpty();
        UUID auditFailure = UUID.randomUUID();
        String failedCode = "production-probe-" + auditFailure.toString().replace("-", "").substring(0, 12);
        try (var owner = DriverManager.getConnection(dbUrl, dbOwner, dbPassword); var ddl = owner.createStatement()) {
            try {
                ddl.execute("CREATE FUNCTION test_probe_audit_fault() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN IF NEW.actor_id LIKE 'probe:%' AND NEW.action='tenant.create' THEN RAISE EXCEPTION 'test audit failure'; END IF; RETURN NEW; END $$");
                ddl.execute("CREATE TRIGGER test_probe_audit_fault BEFORE INSERT ON audit_log FOR EACH ROW EXECUTE FUNCTION test_probe_audit_fault()");
                mvc.perform(post("/v1/setup-probe").header("Authorization", "Bearer " + token(KEY, INSTALL, auditFailure, "survey-setup-probe", "probe.setup")))
                        .andExpect(status().isConflict());
                assertThat(tenants.findByCode(failedCode)).isEmpty();
            } finally {
                ddl.execute("DROP TRIGGER IF EXISTS test_probe_audit_fault ON audit_log");
                ddl.execute("DROP FUNCTION IF EXISTS test_probe_audit_fault()");
            }
        }
        UUID tenantId = UUID.fromString(JsonPath.read(first.getContentAsString(), "$.tenantId"));
        var otherPlan = plans.publish(new PlanDefinition("other-" + UUID.randomUUID().toString().substring(0, 8), Set.of("survey.read"), Map.of(), 1));
        Instant now = Instant.now();
        subscriptions.start(new TenantId(tenantId), otherPlan.id(), SubscriptionKind.PAID, now, now.plusSeconds(3600));
        mvc.perform(post("/v1/setup-probe").header("Authorization", "Bearer " + token)).andExpect(status().isConflict());
        }
    }
    private String token(String key, String installation, UUID run, String audience, String scope) {
        Instant now = Instant.now();
        var claims = JwtClaimsSet.builder().issuer("survey-probe").subject("setup-probe").audience(List.of(audience))
                .issuedAt(now).expiresAt(now.plusSeconds(300)).claim("scope", List.of(scope))
                .claim("installation_id", installation).claim("run_id", run.toString()).claim("engine_id", "production-engine-01").build();
        var encoder = new NimbusJwtEncoder(new ImmutableSecret<>(new SecretKeySpec(key.getBytes(StandardCharsets.UTF_8), "HmacSHA256")));
        return encoder.encode(JwtEncoderParameters.from(JwsHeader.with(MacAlgorithm.HS256).build(), claims)).getTokenValue();
    }
}
