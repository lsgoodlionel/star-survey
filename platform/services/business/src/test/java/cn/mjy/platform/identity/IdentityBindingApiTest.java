package cn.mjy.platform.identity;

import static org.hamcrest.Matchers.not;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

import cn.mjy.platform.shared.TenantId;
import cn.mjy.platform.tenant.TenantFixtures;
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

/**
 * 身份绑定：主体由 (提供方, 企业/应用标识, 外部标识) 三元组唯一确定。
 * 同一个外部标识字符串挂在两个不同应用下是两个人，绝不能合并（openid 作用域陷阱）。
 */
@SpringBootTest
@AutoConfigureMockMvc
class IdentityBindingApiTest {

    private static final String BINDINGS = "/v1/identity-bindings";

    @Autowired
    private MockMvc mvc;

    @Autowired
    private TenantFixtures fixtures;

    private TenantId tenantA;
    private TenantId tenantB;
    private String externalId;

    @BeforeEach
    void setUp() {
        tenantA = fixtures.activeTenant();
        tenantB = fixtures.activeTenant();
        externalId = "o6_bmjrPTlm6_2sgVt7hMZOPfL2M-" + UUID.randomUUID();
    }

    private ResultActions bind(TenantId tenant, String provider, String appId, String external) throws Exception {
        return mvc.perform(post(BINDINGS)
                .header("Authorization", fixtures.userBearer(tenant))
                .contentType(MediaType.APPLICATION_JSON)
                .content("{\"provider\":\"%s\",\"appId\":\"%s\",\"externalId\":\"%s\"}"
                        .formatted(provider, appId, external)));
    }

    private String boundPrincipal(TenantId tenant, String provider, String appId) throws Exception {
        String body = bind(tenant, provider, appId, externalId).andExpect(status().isCreated())
                .andReturn().getResponse().getContentAsString();
        return JsonPath.read(body, "$.principalId");
    }

    private ResultActions lookup(TenantId tenant, String provider, String appId, String external) throws Exception {
        return mvc.perform(get(BINDINGS)
                .param("provider", provider)
                .param("appId", appId)
                .param("externalId", external)
                .header("Authorization", fixtures.userBearer(tenant)));
    }

    @Test
    void bindingAnIdentityCreatesAPrincipalThatCanBeLookedUp() throws Exception {
        String principal = boundPrincipal(tenantA, "wechat_mp", "wx-app-1");

        lookup(tenantA, "wechat_mp", "wx-app-1", externalId)
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.principalId").value(principal))
                .andExpect(jsonPath("$.tenantId").value(tenantA.toString()))
                .andExpect(jsonPath("$.provider").value("wechat_mp"))
                .andExpect(jsonPath("$.appId").value("wx-app-1"))
                .andExpect(jsonPath("$.externalId").value(externalId));
        mvc.perform(get(BINDINGS + "/" + principal).header("Authorization", fixtures.userBearer(tenantA)))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.externalId").value(externalId));
    }

    @Test
    void theSameExternalIdUnderTwoAppsIsTwoDifferentPrincipals() throws Exception {
        String inApp1 = boundPrincipal(tenantA, "wechat_mp", "wx-app-1");
        String inApp2 = boundPrincipal(tenantA, "wechat_mp", "wx-app-2");

        lookup(tenantA, "wechat_mp", "wx-app-1", externalId).andExpect(jsonPath("$.principalId").value(inApp1));
        lookup(tenantA, "wechat_mp", "wx-app-2", externalId)
                .andExpect(jsonPath("$.principalId").value(inApp2))
                .andExpect(jsonPath("$.principalId").value(not(inApp1)));
    }

    @Test
    void theSameExternalIdUnderTwoProvidersIsTwoDifferentPrincipals() throws Exception {
        String wecom = boundPrincipal(tenantA, "wecom", "corp-1");
        String dingtalk = boundPrincipal(tenantA, "dingtalk", "corp-1");

        lookup(tenantA, "dingtalk", "corp-1", externalId)
                .andExpect(jsonPath("$.principalId").value(dingtalk))
                .andExpect(jsonPath("$.principalId").value(not(wecom)));
    }

    @Test
    void bindingTheSameTripleAgainIsIdempotent() throws Exception {
        String principal = boundPrincipal(tenantA, "wecom", "corp-1");

        bind(tenantA, "wecom", "corp-1", externalId)
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.principalId").value(principal));
    }

    /**
     * 同一个外部身份（如平台共用小程序下的同一 openid）可以分别加入两个租户，
     * 成为两个互不相关的主体。若按全局唯一，B 会收到 409，这既拒绝了合法用户，
     * 也泄露了"此人在别的租户存在"。
     */
    @Test
    void theSameExternalIdentityJoinsTwoTenantsAsUnrelatedPrincipals() throws Exception {
        String inA = boundPrincipal(tenantA, "wechat_miniapp", "shared-app");

        String inB = boundPrincipal(tenantB, "wechat_miniapp", "shared-app");

        org.assertj.core.api.Assertions.assertThat(inB).isNotEqualTo(inA);
        lookup(tenantA, "wechat_miniapp", "shared-app", externalId)
                .andExpect(jsonPath("$.principalId").value(inA));
        lookup(tenantB, "wechat_miniapp", "shared-app", externalId)
                .andExpect(jsonPath("$.principalId").value(inB));
    }

    @Test
    void tenantBCannotReadTenantAsBindings() throws Exception {
        String principal = boundPrincipal(tenantA, "feishu", "cli-1");

        lookup(tenantB, "feishu", "cli-1", externalId).andExpect(status().isNotFound());
        mvc.perform(get(BINDINGS + "/" + principal).header("Authorization", fixtures.userBearer(tenantB)))
                .andExpect(status().isNotFound());
    }

    @Test
    void bindingRequestsAreValidated() throws Exception {
        bind(tenantA, "", "corp-1", "x").andExpect(status().isBadRequest());
        bind(tenantA, "WeCom", "corp-1", "x").andExpect(status().isBadRequest());
        bind(tenantA, "wecom", " ", "x").andExpect(status().isBadRequest());
        bind(tenantA, "wecom", "corp-1", "").andExpect(status().isBadRequest());
        bind(tenantA, "wecom", "corp-1", "x".repeat(257)).andExpect(status().isBadRequest());
        lookup(tenantA, "wecom", "corp-1", "").andExpect(status().isBadRequest());
        mvc.perform(get(BINDINGS + "/not-a-uuid").header("Authorization", fixtures.userBearer(tenantA)))
                .andExpect(status().isBadRequest());
    }

    @Test
    void bindingEndpointsRequireAToken() throws Exception {
        mvc.perform(post(BINDINGS).contentType(MediaType.APPLICATION_JSON)
                        .content("{\"provider\":\"wecom\",\"appId\":\"c\",\"externalId\":\"x\"}"))
                .andExpect(status().isUnauthorized());
        mvc.perform(get(BINDINGS + "/" + UUID.randomUUID())).andExpect(status().isUnauthorized());
    }
}
