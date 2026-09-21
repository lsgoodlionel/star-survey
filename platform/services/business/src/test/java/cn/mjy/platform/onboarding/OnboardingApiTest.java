package cn.mjy.platform.onboarding;

import static org.assertj.core.api.Assertions.assertThat;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

import cn.mjy.platform.access.AccessDecisionService;
import cn.mjy.platform.access.Permission;
import cn.mjy.platform.entitlement.SubscriptionService;
import cn.mjy.platform.shared.TenantContext;
import cn.mjy.platform.shared.TenantId;
import cn.mjy.platform.tenant.Tenant;
import cn.mjy.platform.tenant.TenantFixtures;
import com.jayway.jsonpath.JsonPath;
import java.util.List;
import java.util.UUID;
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

    @Test
    void anOperatorPublishesAPlanVersion() throws Exception {
        publishPlan(5).andExpect(status().isCreated())
                .andExpect(jsonPath("$.id").isNotEmpty())
                .andExpect(jsonPath("$.version").value(1));
    }

    @Test
    void onlyOperatorsMayPublishPlans() throws Exception {
        TenantId tenant = fixtures.activeTenant();

        mvc.perform(post(PLANS).header("Authorization", fixtures.userBearer(tenant))
                        .contentType(MediaType.APPLICATION_JSON).content(planBody(5)))
                .andExpect(status().isForbidden());
        mvc.perform(post(PLANS).contentType(MediaType.APPLICATION_JSON).content(planBody(5)))
                .andExpect(status().isUnauthorized());
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
        return mvc.perform(post(PLANS).header("Authorization", fixtures.operatorBearer())
                .contentType(MediaType.APPLICATION_JSON).content(planBody(seats)));
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
