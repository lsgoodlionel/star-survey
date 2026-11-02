package cn.mjy.platform.onboarding;

import static org.assertj.core.api.Assertions.assertThat;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

import cn.mjy.platform.access.AccessDecisionService;
import cn.mjy.platform.access.Permission;
import cn.mjy.platform.entitlement.SubscriptionService;
import cn.mjy.platform.entitlement.SubscriptionKind;
import cn.mjy.platform.shared.TenantContext;
import cn.mjy.platform.shared.TenantId;
import cn.mjy.platform.tenant.Tenant;
import cn.mjy.platform.tenant.TenantFixtures;
import cn.mjy.platform.support.TestTokens;
import com.jayway.jsonpath.JsonPath;
import java.util.List;
import java.util.UUID;
import java.time.Instant;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.webmvc.test.autoconfigure.AutoConfigureMockMvc;
import org.springframework.http.MediaType;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.test.web.servlet.ResultActions;

/**
 * 租户开通编排：发布套餐 → 为处于 provisioning 的租户开通订阅并初始化第一个所有者。
 * 所有者占一个席位，而席位上限来自订阅，所以两步必须在同一事务里：任何一步失败都不留下半开通状态。
 */
@SpringBootTest
@AutoConfigureMockMvc
class OnboardingApiTest {

    private static final String PLANS = "/v1/platform/plans";

    @Autowired
    private MockMvc mvc;

    @Autowired
    private TenantFixtures fixtures;

    @Autowired
    private AccessDecisionService access;

    @Autowired
    private SubscriptionService subscriptions;

    @Autowired
    private TestTokens tokens;

    @Test
    void tenantOwnerCanOnlyReadTheirOwnCurrentSubscription() throws Exception {
        Tenant tenant = fixtures.provisionedTenant();
        Tenant other = fixtures.provisionedTenant();
        String body = planBody(5).replace("100000", "9223372036854775807");
        String code = JsonPath.read(body, "$.planCode");
        String plan = planId(publishPlan(body));
        onboard(tenant.id(), plan, "owner-1").andExpect(status().isOk());
        publishPlan(body.replace("\"member.seats\":5", "\"member.seats\":9"))
                .andExpect(status().isCreated()).andExpect(jsonPath("$.version").value(2));
        String ownerToken = "Bearer " + tokens.issue("owner-1", tenant.id().value(), List.of());

        mvc.perform(get("/v1/subscription").param("tenantId", other.id().toString())
                        .header("Authorization", ownerToken))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.planCode").value(code))
                .andExpect(jsonPath("$.planVersionId").value(plan))
                .andExpect(jsonPath("$.planVersion").value(1))
                .andExpect(jsonPath("$.quotas['member.seats']").value(5))
                .andExpect(jsonPath("$.quotasExact['response.valid_completed']").value("9223372036854775807"))
                .andExpect(jsonPath("$.status").value("TRIAL"));
        mvc.perform(get("/v1/subscription").header("Authorization", fixtures.userBearer(tenant.id())))
                .andExpect(status().isForbidden());
        mvc.perform(get("/v1/subscription")).andExpect(status().isUnauthorized());
        subscriptions.cancel(tenant.id());
        mvc.perform(get("/v1/subscription").header("Authorization", ownerToken))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.status").value("CANCELLED"));
        Instant ended = Instant.now();
        subscriptions.start(tenant.id(), UUID.fromString(plan), SubscriptionKind.PAID, ended, ended);
        mvc.perform(get("/v1/subscription").header("Authorization", ownerToken))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.status").value("EXPIRED"));
    }

    @Test
    void anOperatorPublishesAPlanVersion() throws Exception {
        publishPlan(5).andExpect(status().isCreated())
                .andExpect(jsonPath("$.id").isNotEmpty())
                .andExpect(jsonPath("$.version").value(1));
    }

    @Test
    void anOperatorCanRecoverTheLatestPlanByStableCode() throws Exception {
        String body = planBody(5);
        String code = JsonPath.read(body, "$.planCode");
        publishPlan(body).andExpect(status().isCreated());
        publishPlan(body).andExpect(status().isCreated());

        mvc.perform(get(PLANS + "/" + code).header("Authorization", fixtures.operatorBearer()))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.planCode").value(code))
                .andExpect(jsonPath("$.version").value(2));
        mvc.perform(get(PLANS + "/missing-plan").header("Authorization", fixtures.operatorBearer()))
                .andExpect(status().isNotFound());
    }

    @Test
    void operatorCanBrowseBoundedLatestPlanVersionsWithoutExposingThemToTenants() throws Exception {
        String body = planBody(5).replace("100000", "9223372036854775807");
        String code = JsonPath.read(body, "$.planCode");
        String nextCode = code + "z";
        publishPlan(body).andExpect(status().isCreated());
        publishPlan(body).andExpect(status().isCreated());
        publishPlan(body.replace(code, nextCode).replace("9223372036854775807", "9007199254740993")).andExpect(status().isCreated());
        mvc.perform(get("/v1/platform/plan-catalog").param("after", code.substring(0, code.length() - 1)).param("limit", "1")
                        .header("Authorization", fixtures.operatorBearer()))
                .andExpect(status().isOk()).andExpect(jsonPath("$.items[0].planCode").value(code))
                .andExpect(jsonPath("$.items[0].version").value(2))
                .andExpect(jsonPath("$.items[0].quotas['member.seats']").value("5"))
                .andExpect(jsonPath("$.items[0].quotas['response.valid_completed']").value("9223372036854775807"))
                .andExpect(jsonPath("$.items[0].exportWindowDays").value(30))
                .andExpect(jsonPath("$.nextCursor").value(code));
        mvc.perform(get("/v1/platform/plan-catalog").param("after", code).param("limit", "1")
                        .header("Authorization", fixtures.operatorBearer()))
                .andExpect(status().isOk()).andExpect(jsonPath("$.items[0].planCode").value(nextCode))
                .andExpect(jsonPath("$.items[0].quotas['response.valid_completed']").value("9007199254740993"));
        mvc.perform(get("/v1/platform/plan-catalog").header("Authorization", fixtures.userBearer(fixtures.activeTenant())))
                .andExpect(status().isForbidden());
        mvc.perform(get("/v1/platform/plan-catalog")).andExpect(status().isUnauthorized());
        mvc.perform(get("/v1/platform/plan-catalog").param("limit", "101").header("Authorization", fixtures.operatorBearer()))
                .andExpect(status().isBadRequest());
        mvc.perform(get("/v1/platform/plan-catalog").param("after", "bad/cursor").header("Authorization", fixtures.operatorBearer()))
                .andExpect(status().isBadRequest());
    }

    @Test
    void onlyOperatorsMayPublishPlans() throws Exception {
        TenantId tenant = fixtures.activeTenant();

        mvc.perform(post(PLANS).header("Authorization", fixtures.userBearer(tenant))
                        .contentType(MediaType.APPLICATION_JSON).content(planBody(5)))
                .andExpect(status().isForbidden());
        mvc.perform(post(PLANS).contentType(MediaType.APPLICATION_JSON).content(planBody(5)))
                .andExpect(status().isUnauthorized());
        mvc.perform(get(PLANS + "/forbidden").header("Authorization", fixtures.userBearer(tenant)))
                .andExpect(status().isForbidden());
        mvc.perform(get(PLANS + "/forbidden")).andExpect(status().isUnauthorized());
    }

    @Test
    void onboardingStartsTheSubscriptionAndMakesTheOwnerAnAdministrator() throws Exception {
        Tenant tenant = fixtures.provisionedTenant();
        String plan = planId(publishPlan(5));

        onboard(tenant.id(), plan, "owner-1").andExpect(status().isOk())
                .andExpect(jsonPath("$.ownerActorId").value("owner-1"));

        assertThat(subscriptions.current(tenant.id())).isPresent();
        assertThat(access.canInTenant(owner(tenant.id(), "owner-1"), Permission.MANAGE_MEMBERS).allowed()).isTrue();
    }

    @Test
    void aTenantCanOnlyBeOnboardedOnce() throws Exception {
        Tenant tenant = fixtures.provisionedTenant();
        String plan = planId(publishPlan(5));
        onboard(tenant.id(), plan, "owner-1").andExpect(status().isOk());

        onboard(tenant.id(), plan, "owner-2").andExpect(status().isConflict())
                .andExpect(jsonPath("$.error").value("already_onboarded"));
    }

    @Test
    void aPlanWithoutSeatsIsRejectedAndLeavesNothingBehind() throws Exception {
        Tenant tenant = fixtures.provisionedTenant();
        String plan = planId(publishPlan(0));

        onboard(tenant.id(), plan, "owner-1").andExpect(status().isUnprocessableEntity())
                .andExpect(jsonPath("$.error").value("plan_has_no_seats"));

        assertThat(subscriptions.current(tenant.id())).isEmpty();
    }

    @Test
    void onlyAProvisioningTenantCanBeOnboarded() throws Exception {
        TenantId active = fixtures.activeTenant();
        String plan = planId(publishPlan(5));

        onboard(active, plan, "owner-1").andExpect(status().isConflict())
                .andExpect(jsonPath("$.error").value("tenant_not_provisioning"));
    }

    @Test
    void onboardingIsOperatorOnly() throws Exception {
        Tenant tenant = fixtures.provisionedTenant();
        String plan = planId(publishPlan(5));

        mvc.perform(post(onboardingPath(tenant.id())).header("Authorization", fixtures.userBearer(tenant.id()))
                        .contentType(MediaType.APPLICATION_JSON).content(onboardingBody(plan, "owner-1")))
                .andExpect(status().isForbidden());
        assertThat(subscriptions.current(tenant.id())).isEmpty();
    }

    @Test
    void theOwnerOfOneTenantHasNothingInAnother() throws Exception {
        Tenant a = fixtures.provisionedTenant();
        Tenant b = fixtures.provisionedTenant();
        String plan = planId(publishPlan(5));
        onboard(a.id(), plan, "owner-1").andExpect(status().isOk());

        assertThat(access.canInTenant(owner(b.id(), "owner-1"), Permission.MANAGE_MEMBERS).allowed()).isFalse();
    }

    private ResultActions publishPlan(long seats) throws Exception {
        return publishPlan(planBody(seats));
    }

    private ResultActions publishPlan(String body) throws Exception {
        return mvc.perform(post(PLANS).header("Authorization", fixtures.operatorBearer())
                .contentType(MediaType.APPLICATION_JSON).content(body));
    }

    private ResultActions onboard(TenantId tenant, String planId, String owner) throws Exception {
        return mvc.perform(post(onboardingPath(tenant)).header("Authorization", fixtures.operatorBearer())
                .contentType(MediaType.APPLICATION_JSON).content(onboardingBody(planId, owner)));
    }

    private static String onboardingPath(TenantId tenant) {
        return "/v1/platform/tenants/" + tenant + "/onboarding";
    }

    private static String planBody(long seats) {
        String code = "plan-" + UUID.randomUUID().toString().substring(0, 8);
        return """
                {"planCode":"%s","capabilities":["survey.read","survey.write","response.collect"],
                 "quotas":{"member.seats":%d,"response.valid_completed":100000},"exportWindowDays":30}
                """.formatted(code, seats);
    }

    private static String onboardingBody(String planId, String owner) {
        return """
                {"planVersionId":"%s","kind":"TRIAL","days":14,"ownerActorId":"%s"}
                """.formatted(planId, owner);
    }

    private static String planId(ResultActions published) throws Exception {
        return JsonPath.read(published.andReturn().getResponse().getContentAsString(), "$.id");
    }

    private static TenantContext owner(TenantId tenant, String actor) {
        return new TenantContext(tenant, actor, List.of(), "trace-" + UUID.randomUUID());
    }
}
