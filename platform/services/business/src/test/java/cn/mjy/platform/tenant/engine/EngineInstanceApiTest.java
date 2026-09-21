package cn.mjy.platform.tenant.engine;

import static org.assertj.core.api.Assertions.assertThat;
import static org.hamcrest.Matchers.hasSize;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

import cn.mjy.platform.shared.TenantId;
import cn.mjy.platform.shared.tenant.TenantScope;
import cn.mjy.platform.tenant.TenantFixtures;
import cn.mjy.platform.tenant.TenantService;
import cn.mjy.platform.tenant.TenantStatus;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.webmvc.test.autoconfigure.AutoConfigureMockMvc;
import org.springframework.http.MediaType;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.test.web.servlet.ResultActions;

/** 引擎实例登记（运营端）与租户自查（租户端）。 */
@SpringBootTest
@AutoConfigureMockMvc
class EngineInstanceApiTest {

    @Autowired
    private MockMvc mvc;

    @Autowired
    private TenantFixtures fixtures;

    @Autowired
    private TenantService tenants;

    @Autowired
    private TenantScope tenantScope;

    @Autowired
    private JdbcClient jdbc;

    private TenantId tenantA;
    private TenantId tenantB;

    @BeforeEach
    void setUp() {
        tenantA = fixtures.activeTenant();
        tenantB = fixtures.activeTenant();
    }

    private static String platformPath(TenantId tenant) {
        return "/v1/platform/tenants/" + tenant + "/engine-instances";
    }

    private ResultActions register(String bearer, TenantId tenant, String id, String baseUrl) throws Exception {
        return mvc.perform(post(platformPath(tenant))
                .header("Authorization", bearer)
                .contentType(MediaType.APPLICATION_JSON)
                .content("{\"id\":\"%s\",\"baseUrl\":\"%s\"}".formatted(id, baseUrl)));
    }

    private ResultActions changeStatus(String bearer, TenantId tenant, String id, String target) throws Exception {
        return mvc.perform(post(platformPath(tenant) + "/" + id + "/status")
                .header("Authorization", bearer)
                .contentType(MediaType.APPLICATION_JSON)
                .content("{\"status\":\"%s\"}".formatted(target)));
    }

    private String registeredInstanceOf(TenantId tenant) throws Exception {
        String id = EngineFixtures.uniqueInstanceId();
        register(fixtures.operatorBearer(), tenant, id, "https://engine.example/" + id).andExpect(status().isCreated());
        return id;
    }

    @Test
    void anOperatorRegistersAnInstanceAndItIsAudited() throws Exception {
        String id = EngineFixtures.uniqueInstanceId();

        register(fixtures.operatorBearer(), tenantA, id, "https://a.engine.example:8443/survey")
                .andExpect(status().isCreated())
                .andExpect(jsonPath("$.id").value(id))
                .andExpect(jsonPath("$.tenantId").value(tenantA.toString()))
                .andExpect(jsonPath("$.baseUrl").value("https://a.engine.example:8443/survey"))
                .andExpect(jsonPath("$.status").value("active"));

        mvc.perform(get(platformPath(tenantA)).header("Authorization", fixtures.operatorBearer()))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$", hasSize(1)))
                .andExpect(jsonPath("$[0].id").value(id));
        assertThat(tenantScope.call(tenantA, () -> jdbc
                .sql("SELECT resource FROM audit_log WHERE action = 'engine_instance.register'")
                .query(String.class).list()))
                .containsExactly("engine_instance/" + id);
    }

    @Test
    void instanceStatusFollowsItsLifecycleAndRetiredIsTerminal() throws Exception {
        String id = registeredInstanceOf(tenantA);
        String operator = fixtures.operatorBearer();

        changeStatus(operator, tenantA, id, "draining").andExpect(status().isOk()).andExpect(jsonPath("$.status").value("draining"));
        changeStatus(operator, tenantA, id, "active").andExpect(status().isOk());
        changeStatus(operator, tenantA, id, "retired").andExpect(status().isOk());
        changeStatus(operator, tenantA, id, "active")
                .andExpect(status().isConflict())
                .andExpect(jsonPath("$.error").value("illegal_transition"));
        changeStatus(operator, tenantA, id, "unknown").andExpect(status().isBadRequest());
    }

    @Test
    void registrationIsValidated() throws Exception {
        String operator = fixtures.operatorBearer();

        register(operator, tenantA, "Bad Id", "https://ok.example").andExpect(status().isBadRequest());
        register(operator, tenantA, EngineFixtures.uniqueInstanceId(), "ftp://engine.example").andExpect(status().isBadRequest());
        register(operator, tenantA, EngineFixtures.uniqueInstanceId(), "not a url").andExpect(status().isBadRequest());
        register(operator, TenantId.random(), EngineFixtures.uniqueInstanceId(), "https://ok.example")
                .andExpect(status().isNotFound());
    }

    @Test
    void anInstanceIdBelongsToExactlyOneTenant() throws Exception {
        String id = registeredInstanceOf(tenantA);

        register(fixtures.operatorBearer(), tenantB, id, "https://b.engine.example").andExpect(status().isConflict());
    }

    @Test
    void aClosedTenantCannotGetNewInstances() throws Exception {
        tenants.changeStatus(tenantB, TenantStatus.CLOSED, "op", "trace");

        register(fixtures.operatorBearer(), tenantB, EngineFixtures.uniqueInstanceId(), "https://b.engine.example")
                .andExpect(status().isConflict());
    }

    @Test
    void aTenantSeesOnlyItsOwnInstances() throws Exception {
        String instanceOfA = registeredInstanceOf(tenantA);
        String instanceOfB = registeredInstanceOf(tenantB);

        mvc.perform(get("/v1/engine-instances").header("Authorization", fixtures.userBearer(tenantA)))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$", hasSize(1)))
                .andExpect(jsonPath("$[0].id").value(instanceOfA));
        mvc.perform(get("/v1/engine-instances").header("Authorization", fixtures.userBearer(tenantB)))
                .andExpect(jsonPath("$", hasSize(1)))
                .andExpect(jsonPath("$[0].id").value(instanceOfB));
    }

    @Test
    void tenantBCannotReadTenantAsInstanceById() throws Exception {
        String instanceOfA = registeredInstanceOf(tenantA);

        mvc.perform(get("/v1/engine-instances/" + instanceOfA).header("Authorization", fixtures.userBearer(tenantA)))
                .andExpect(status().isOk());
        mvc.perform(get("/v1/engine-instances/" + instanceOfA).header("Authorization", fixtures.userBearer(tenantB)))
                .andExpect(status().isNotFound());
    }

    @Test
    void operatorsCannotChangeAnInstanceThroughTheWrongTenantsPath() throws Exception {
        String instanceOfA = registeredInstanceOf(tenantA);

        changeStatus(fixtures.operatorBearer(), tenantB, instanceOfA, "retired").andExpect(status().isNotFound());
        mvc.perform(get("/v1/engine-instances/" + instanceOfA).header("Authorization", fixtures.userBearer(tenantA)))
                .andExpect(jsonPath("$.status").value("active"));
    }

    @Test
    void tenantTokensAreForbiddenOnOperatorInstanceEndpoints() throws Exception {
        String instanceOfA = registeredInstanceOf(tenantA);
        String user = fixtures.userBearer(tenantA);

        register(user, tenantA, EngineFixtures.uniqueInstanceId(), "https://a.engine.example").andExpect(status().isForbidden());
        changeStatus(user, tenantA, instanceOfA, "retired").andExpect(status().isForbidden());
        mvc.perform(get(platformPath(tenantA)).header("Authorization", user)).andExpect(status().isForbidden());
    }

    @Test
    void instanceEndpointsRequireAToken() throws Exception {
        mvc.perform(get(platformPath(tenantA))).andExpect(status().isUnauthorized());
        mvc.perform(post(platformPath(tenantA)).contentType(MediaType.APPLICATION_JSON)
                        .content("{\"id\":\"eng-x1\",\"baseUrl\":\"https://x.example\"}"))
                .andExpect(status().isUnauthorized());
        mvc.perform(get("/v1/engine-instances")).andExpect(status().isUnauthorized());
    }
}
