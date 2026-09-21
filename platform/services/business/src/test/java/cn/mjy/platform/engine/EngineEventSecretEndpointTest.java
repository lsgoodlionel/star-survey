package cn.mjy.platform.engine;

import static cn.mjy.platform.engine.support.PhpEnvelope.COMPLETED;
import static cn.mjy.platform.engine.support.PhpEnvelope.event;
import static org.assertj.core.api.Assertions.assertThat;
import static org.hamcrest.Matchers.containsString;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.header;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

import cn.mjy.platform.engine.support.EngineEventsIntegrationTest;
import cn.mjy.platform.engine.support.EngineRows;
import cn.mjy.platform.engine.support.FakeEngineInstanceDirectory;
import cn.mjy.platform.engine.support.PhpEventSigner;
import cn.mjy.platform.engine.support.SignedEventRequests;
import cn.mjy.platform.shared.TenantId;
import cn.mjy.platform.shared.tenant.TenantScope;
import cn.mjy.platform.support.TestTokens;
import cn.mjy.platform.tenant.PlatformRoles;
import com.jayway.jsonpath.JsonPath;
import java.time.Instant;
import java.util.List;
import java.util.Map;
import java.util.UUID;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.test.web.servlet.ResultActions;

/**
 * 运营端签发实例密钥：POST /v1/platform/engine-instances/{instanceId}/event-secret。
 * 只有平台运营能取（租户令牌 403、无令牌 401）；返回值就是验签时为该实例派生的密钥；每次签发都留审计。
 */
@EngineEventsIntegrationTest
class EngineEventSecretEndpointTest {

    private static final String OPERATOR = "operator-secret-test";

    @Autowired
    private MockMvc mvc;

    @Autowired
    private TestTokens tokens;

    @Autowired
    private FakeEngineInstanceDirectory directory;

    @Autowired
    private EngineEventKeys keys;

    @Autowired
    private EngineRows rows;

    @Autowired
    private TenantScope scope;

    @Autowired
    private JdbcClient jdbc;

    private TenantId tenant;
    private String instance;

    @BeforeEach
    void registerEngine() {
        tenant = TenantId.random();
        instance = directory.register(tenant);
    }

    private static String path(String instanceId) {
        return "/v1/platform/engine-instances/" + instanceId + "/event-secret";
    }

    private String operatorBearer() {
        return "Bearer " + tokens.issue(OPERATOR, UUID.randomUUID(), List.of(PlatformRoles.OPERATOR));
    }

    private ResultActions issue(String bearer, String instanceId) throws Exception {
        return mvc.perform(post(path(instanceId)).header("Authorization", bearer));
    }

    private List<Map<String, Object>> auditRows() {
        return scope.call(tenant, () -> jdbc.sql("""
                        SELECT actor_id, action FROM audit_log WHERE resource = :resource ORDER BY id
                        """)
                .param("resource", "engine_instance/" + instance)
                .query().listOfRows());
    }

    @Test
    void anOperatorReceivesTheSecretTheVerifierDerivesForThatInstance() throws Exception {
        issue(operatorBearer(), instance)
                .andExpect(status().isOk())
                .andExpect(header().string("Cache-Control", containsString("no-store")))
                .andExpect(jsonPath("$.engineInstanceId").value(instance))
                .andExpect(jsonPath("$.secret").value(keys.derivedSecret(instance)))
                .andExpect(jsonPath("$.secret").value(SignedEventRequests.secretFor(instance)));
    }

    @Test
    void everyIssueIsAudited() throws Exception {
        issue(operatorBearer(), instance).andExpect(status().isOk());

        assertThat(auditRows()).singleElement().satisfies(row -> {
            assertThat(row).containsEntry("actor_id", OPERATOR);
            assertThat(row).containsEntry("action", EngineEventSecretController.AUDIT_ACTION);
        });
    }

    @Test
    void theIssuedSecretIsAcceptedForThatInstanceOnly() throws Exception {
        String body = issue(operatorBearer(), instance).andReturn().getResponse().getContentAsString();
        String secret = JsonPath.read(body, "$.secret");
        byte[] batch = SignedEventRequests.body(event(COMPLETED).instance(instance).response(1));
        String timestamp = Long.toString(Instant.now().getEpochSecond());
        String signature = PhpEventSigner.sign(secret, timestamp, batch);

        mvc.perform(SignedEventRequests.raw(batch, instance, timestamp, signature)).andExpect(status().isOk());
        mvc.perform(SignedEventRequests.raw(batch, directory.register(TenantId.random()), timestamp, signature))
                .andExpect(status().isUnauthorized());
        assertThat(rows.inbox(tenant)).isEqualTo(1);
    }

    @Test
    void aTenantTokenIsForbiddenAndNothingIsAudited() throws Exception {
        String tenantUser = "Bearer " + tokens.issue("user-1", tenant.value(), List.of("admin", "owner"));

        issue(tenantUser, instance).andExpect(status().isForbidden());

        assertThat(auditRows()).isEmpty();
    }

    @Test
    void aRequestWithoutATokenIsUnauthorized() throws Exception {
        mvc.perform(post(path(instance))).andExpect(status().isUnauthorized());

        assertThat(auditRows()).isEmpty();
    }

    @Test
    void anUnregisteredInstanceGetsNoSecret() throws Exception {
        issue(operatorBearer(), "engine-never-registered-" + UUID.randomUUID())
                .andExpect(status().isNotFound())
                .andExpect(jsonPath("$.error").value("unknown_engine_instance"))
                .andExpect(jsonPath("$.secret").doesNotExist());
    }
}
