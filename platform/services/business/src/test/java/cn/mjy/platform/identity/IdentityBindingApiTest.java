package cn.mjy.platform.identity;

import static org.hamcrest.Matchers.not;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

import cn.mjy.platform.access.AccessFixture;
import cn.mjy.platform.shared.TenantContext;
import cn.mjy.platform.shared.TenantId;
import cn.mjy.platform.support.TestTokens;
import cn.mjy.platform.tenant.TenantFixtures;
import com.jayway.jsonpath.JsonPath;
import java.util.List;
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
 *
 * <p>权限：建绑定、按身份反查、读别人的绑定都需要租户级 manage-members（真实的在职管理员）；
 * 令牌主体（sub）等于该主体标识时可以读自己的绑定。令牌里的 roles 声明不参与判定。
 */
@SpringBootTest
@AutoConfigureMockMvc
class IdentityBindingApiTest {

    private static final String BINDINGS = "/v1/identity-bindings";
    private static final String ADMIN = "org-admin";
    private static final String PLAIN_MEMBER = "plain-member";
    private static final String PROJECT_MANAGER = "project-manager";

    @Autowired
    private MockMvc mvc;

    @Autowired
    private TenantFixtures fixtures;

    @Autowired
    private AccessFixture access;

    @Autowired
    private TestTokens tokens;

    private TenantId tenantA;
    private TenantId tenantB;
    private String externalId;

    @BeforeEach
    void setUp() {
        tenantA = tenantWithMembers();
        tenantB = tenantWithMembers();
        externalId = "o6_bmjrPTlm6_2sgVt7hMZOPfL2M-" + UUID.randomUUID();
    }

    /** 真实租户：所有者 + 组织管理员（租户级 manage-members）+ 无授权的普通成员 + 只在项目上管理成员的项目管理者。 */
    private TenantId tenantWithMembers() {
        TenantId tenant = fixtures.activeTenant();
        access.members().bootstrapOwner(tenant, AccessFixture.OWNER, "trace-bootstrap");
        TenantContext owner = AccessFixture.context(tenant, AccessFixture.OWNER);
        access.member(owner, ADMIN, "org_admin", null);
        access.activeMember(owner, PLAIN_MEMBER);
        access.member(owner, PROJECT_MANAGER, "project_manager", access.project(tenant));
        return tenant;
    }

    private String bearer(TenantId tenant, String actor) {
        // 故意带上看起来很"大"的角色声明：判定只看成员与授权，不看令牌里的 roles。
        return "Bearer " + tokens.issue(actor, tenant.value(), List.of("admin", "tenant_owner"));
    }

    private ResultActions bind(TenantId tenant, String actor, String provider, String appId, String external)
            throws Exception {
        return mvc.perform(post(BINDINGS)
                .header("Authorization", bearer(tenant, actor))
                .contentType(MediaType.APPLICATION_JSON)
                .content("{\"provider\":\"%s\",\"appId\":\"%s\",\"externalId\":\"%s\"}"
                        .formatted(provider, appId, external)));
    }

    private ResultActions bind(TenantId tenant, String provider, String appId, String external) throws Exception {
        return bind(tenant, ADMIN, provider, appId, external);
    }

    private String boundPrincipal(TenantId tenant, String provider, String appId) throws Exception {
        String body = bind(tenant, provider, appId, externalId).andExpect(status().isCreated())
                .andReturn().getResponse().getContentAsString();
        return JsonPath.read(body, "$.principalId");
    }

    private ResultActions lookup(TenantId tenant, String actor, String provider, String appId, String external)
            throws Exception {
        return mvc.perform(get(BINDINGS)
                .param("provider", provider)
                .param("appId", appId)
                .param("externalId", external)
                .header("Authorization", bearer(tenant, actor)));
    }

    private ResultActions lookup(TenantId tenant, String provider, String appId, String external) throws Exception {
        return lookup(tenant, ADMIN, provider, appId, external);
    }

    private ResultActions read(TenantId tenant, String actor, String principal) throws Exception {
        return mvc.perform(get(BINDINGS + "/" + principal).header("Authorization", bearer(tenant, actor)));
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
        read(tenantA, ADMIN, principal)
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

    /** 跨租户：B 的真实管理员也读不到 A 的绑定，且得到 404（与不存在不可区分）。 */
    @Test
    void tenantBCannotReadTenantAsBindings() throws Exception {
        String principal = boundPrincipal(tenantA, "feishu", "cli-1");

        lookup(tenantB, "feishu", "cli-1", externalId).andExpect(status().isNotFound());
        read(tenantB, ADMIN, principal).andExpect(status().isNotFound());
    }

    @Test
    void aPlainMemberCannotCreateBindings() throws Exception {
        bind(tenantA, PLAIN_MEMBER, "wecom", "corp-1", externalId).andExpect(status().isForbidden());

        lookup(tenantA, "wecom", "corp-1", externalId).andExpect(status().isNotFound());
    }

    /** 只在某个项目上有 manage-members 不等于租户级管理成员。 */
    @Test
    void aProjectScopedMemberManagerCannotCreateBindings() throws Exception {
        bind(tenantA, PROJECT_MANAGER, "wecom", "corp-1", externalId).andExpect(status().isForbidden());
    }

    @Test
    void aPlainMemberCannotReadOrLookUpOtherPrincipals() throws Exception {
        String principal = boundPrincipal(tenantA, "wecom", "corp-1");

        read(tenantA, PLAIN_MEMBER, principal).andExpect(status().isForbidden());
        lookup(tenantA, PLAIN_MEMBER, "wecom", "corp-1", externalId).andExpect(status().isForbidden());
        // 未知主体同样 403：判定先于查询，不给出"是否存在"的线索。
        read(tenantA, PLAIN_MEMBER, UUID.randomUUID().toString()).andExpect(status().isForbidden());
    }

    /** 令牌对该租户有效、但持有者不是其成员：什么都不能做。 */
    @Test
    void aNonMemberTokenCannotCreateReadOrLookUp() throws Exception {
        String principal = boundPrincipal(tenantA, "wecom", "corp-1");

        bind(tenantA, "stranger", "wecom", "corp-1", "someone-else").andExpect(status().isForbidden());
        read(tenantA, "stranger", principal).andExpect(status().isForbidden());
        lookup(tenantA, "stranger", "wecom", "corp-1", externalId).andExpect(status().isForbidden());
    }

    /** 主体本人（令牌 sub = 主体标识，组织免登签发的令牌即如此）可以读自己的绑定，但不能读别人的。 */
    @Test
    void aPrincipalCanReadItsOwnBindingButNotOthers() throws Exception {
        String own = boundPrincipal(tenantA, "wecom", "corp-1");
        String other = boundPrincipal(tenantA, "wecom", "corp-2");

        read(tenantA, own, own)
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.principalId").value(own))
                .andExpect(jsonPath("$.externalId").value(externalId));
        read(tenantA, own.toUpperCase(), own).andExpect(status().isForbidden());
        read(tenantA, own, other).andExpect(status().isForbidden());
        lookup(tenantA, own, "wecom", "corp-1", externalId).andExpect(status().isForbidden());
    }

    /** 同一个主体标识放进别的租户的令牌里读不到任何东西。 */
    @Test
    void ownershipDoesNotCrossTenants() throws Exception {
        String own = boundPrincipal(tenantA, "wecom", "corp-1");

        read(tenantB, own, own).andExpect(status().isNotFound());
    }

    @Test
    void bindingRequestsAreValidated() throws Exception {
        bind(tenantA, "", "corp-1", "x").andExpect(status().isBadRequest());
        bind(tenantA, "WeCom", "corp-1", "x").andExpect(status().isBadRequest());
        bind(tenantA, "wecom", " ", "x").andExpect(status().isBadRequest());
        bind(tenantA, "wecom", "corp-1", "").andExpect(status().isBadRequest());
        bind(tenantA, "wecom", "corp-1", "x".repeat(257)).andExpect(status().isBadRequest());
        lookup(tenantA, "wecom", "corp-1", "").andExpect(status().isBadRequest());
        read(tenantA, ADMIN, "not-a-uuid").andExpect(status().isBadRequest());
    }

    @Test
    void bindingEndpointsRequireAToken() throws Exception {
        mvc.perform(post(BINDINGS).contentType(MediaType.APPLICATION_JSON)
                        .content("{\"provider\":\"wecom\",\"appId\":\"c\",\"externalId\":\"x\"}"))
                .andExpect(status().isUnauthorized());
        mvc.perform(get(BINDINGS + "/" + UUID.randomUUID())).andExpect(status().isUnauthorized());
    }
}
