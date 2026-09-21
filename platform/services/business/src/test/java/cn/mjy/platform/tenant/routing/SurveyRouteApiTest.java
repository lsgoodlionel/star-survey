package cn.mjy.platform.tenant.routing;

import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

import cn.mjy.platform.shared.TenantId;
import cn.mjy.platform.tenant.TenantFixtures;
import cn.mjy.platform.tenant.engine.EngineFixtures;
import cn.mjy.platform.tenant.engine.EngineInstanceService;
import cn.mjy.platform.tenant.engine.EngineInstanceStatus;
import com.jayway.jsonpath.JsonPath;
import java.util.UUID;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.webmvc.test.autoconfigure.AutoConfigureMockMvc;
import org.springframework.http.MediaType;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.test.web.servlet.ResultActions;

/** 问卷路由：对外问卷 UUID ⇄ 引擎实例 ⇄ 引擎 sid，按租户隔离。 */
@SpringBootTest
@AutoConfigureMockMvc
class SurveyRouteApiTest {

    private static final String ROUTES = "/v1/survey-routes";
    /** ADR 0002：两个租户出现同号 sid 是正常状态。 */
    private static final int SHARED_SID = 555001;

    @Autowired
    private MockMvc mvc;

    @Autowired
    private TenantFixtures fixtures;

    @Autowired
    private EngineInstanceService instances;

    private TenantId tenantA;
    private TenantId tenantB;
    private String instanceOfA;
    private String instanceOfB;

    @BeforeEach
    void setUp() {
        tenantA = fixtures.activeTenant();
        tenantB = fixtures.activeTenant();
        instanceOfA = register(tenantA);
        instanceOfB = register(tenantB);
    }

    private String register(TenantId tenant) {
        String id = EngineFixtures.uniqueInstanceId();
        instances.register(tenant, id, "https://engine.example/" + id, "op", "trace");
        return id;
    }

    private ResultActions createRoute(TenantId tenant, String instanceId, int sid) throws Exception {
        return mvc.perform(post(ROUTES)
                .header("Authorization", fixtures.userBearer(tenant))
                .contentType(MediaType.APPLICATION_JSON)
                .content("{\"engineInstanceId\":\"%s\",\"engineSid\":%d}".formatted(instanceId, sid)));
    }

    private String createdRoute(TenantId tenant, String instanceId, int sid) throws Exception {
        String body = createRoute(tenant, instanceId, sid).andExpect(status().isCreated())
                .andReturn().getResponse().getContentAsString();
        return JsonPath.read(body, "$.publicId");
    }

    private ResultActions getRoute(TenantId tenant, String publicId) throws Exception {
        return mvc.perform(get(ROUTES + "/" + publicId).header("Authorization", fixtures.userBearer(tenant)));
    }

    private ResultActions findByEngine(TenantId tenant, String instanceId, int sid) throws Exception {
        return mvc.perform(get(ROUTES)
                .param("engineInstanceId", instanceId)
                .param("engineSid", String.valueOf(sid))
                .header("Authorization", fixtures.userBearer(tenant)));
    }

    @Test
    void aTenantCreatesARouteAndResolvesItBothWays() throws Exception {
        String publicId = createdRoute(tenantA, instanceOfA, 123456);

        getRoute(tenantA, publicId)
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.publicId").value(publicId))
                .andExpect(jsonPath("$.tenantId").value(tenantA.toString()))
                .andExpect(jsonPath("$.engineInstanceId").value(instanceOfA))
                .andExpect(jsonPath("$.engineSid").value(123456));
        findByEngine(tenantA, instanceOfA, 123456)
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.publicId").value(publicId));
    }

    @Test
    void creatingTheSameRouteTwiceIsIdempotent() throws Exception {
        String publicId = createdRoute(tenantA, instanceOfA, 42);

        createRoute(tenantA, instanceOfA, 42)
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.publicId").value(publicId));
    }

    @Test
    void theSameSidOnTwoTenantsInstancesNeverCrosses() throws Exception {
        String routeOfA = createdRoute(tenantA, instanceOfA, SHARED_SID);
        String routeOfB = createdRoute(tenantB, instanceOfB, SHARED_SID);

        findByEngine(tenantA, instanceOfA, SHARED_SID).andExpect(jsonPath("$.publicId").value(routeOfA));
        findByEngine(tenantB, instanceOfB, SHARED_SID).andExpect(jsonPath("$.publicId").value(routeOfB));
    }

    @Test
    void tenantBCannotLookUpTenantAsRouteByPublicId() throws Exception {
        String routeOfA = createdRoute(tenantA, instanceOfA, 7);

        getRoute(tenantB, routeOfA).andExpect(status().isNotFound());
    }

    @Test
    void tenantBCannotLookUpTenantAsRouteByEngineCoordinates() throws Exception {
        createdRoute(tenantA, instanceOfA, 8);

        findByEngine(tenantB, instanceOfA, 8).andExpect(status().isNotFound());
    }

    @Test
    void tenantBCannotRouteToTenantAsInstance() throws Exception {
        createRoute(tenantB, instanceOfA, 9).andExpect(status().isNotFound());

        findByEngine(tenantA, instanceOfA, 9).andExpect(status().isNotFound());
    }

    @Test
    void routesCanOnlyPointAtActiveInstances() throws Exception {
        instances.changeStatus(tenantA, instanceOfA, EngineInstanceStatus.DRAINING, "op", "trace");

        createRoute(tenantA, instanceOfA, 10).andExpect(status().isConflict());
    }

    @Test
    void routeRequestsAreValidated() throws Exception {
        createRoute(tenantA, instanceOfA, 0).andExpect(status().isBadRequest());
        createRoute(tenantA, instanceOfA, -5).andExpect(status().isBadRequest());
        createRoute(tenantA, "", 5).andExpect(status().isBadRequest());
        getRoute(tenantA, "not-a-uuid").andExpect(status().isBadRequest());
        findByEngine(tenantA, instanceOfA, -1).andExpect(status().isBadRequest());
        getRoute(tenantA, UUID.randomUUID().toString()).andExpect(status().isNotFound());
    }

    @Test
    void routeEndpointsRequireAToken() throws Exception {
        mvc.perform(get(ROUTES + "/" + UUID.randomUUID())).andExpect(status().isUnauthorized());
        mvc.perform(post(ROUTES).contentType(MediaType.APPLICATION_JSON)
                        .content("{\"engineInstanceId\":\"eng-x1\",\"engineSid\":1}"))
                .andExpect(status().isUnauthorized());
    }
}
