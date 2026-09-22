package cn.mjy.platform.identity.org;

import static org.assertj.core.api.Assertions.assertThat;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.header;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

import cn.mjy.platform.shared.TenantId;
import com.jayway.jsonpath.JsonPath;
import jakarta.servlet.http.Cookie;
import java.util.Map;
import java.util.UUID;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.ValueSource;

/**
 * 企业微信 / 钉钉 / 飞书免登：发起（state + 浏览器绑定）→ 开放平台 → 回调在服务端兑换授权码 → 解析组织成员
 * → 映射到身份绑定 → 签发平台令牌。state 一次性、限时、绑定租户与浏览器。
 */
class OrgLoginFlowTest extends OrgLoginTestSupport {

    @ParameterizedTest
    @ValueSource(strings = {"wecom", "dingtalk", "feishu"})
    void aPreauthorizedOrgMemberSignsInAndGetsAPlatformToken(String provider) throws Exception {
        TenantId tenant = newTenant();
        Org org = connect(tenant, provider, null);
        String userId = unique("u");
        FAKE.addUser(provider, org.corp(), userId);
        String principal = preauthorize(org, userId);

        Started started = start(tenant, org.connectionId());
        String code = FAKE.issueCode(provider, org.corp(), org.appId(), userId);
        String body = callback(tenant, org.connectionId(), code, started)
                .andExpect(status().isOk())
                .andExpect(header().string("Cache-Control", "no-store"))
                .andExpect(jsonPath("$.tokenType").value("Bearer"))
                .andExpect(jsonPath("$.principalId").value(principal))
                .andExpect(jsonPath("$.tenantId").value(tenant.toString()))
                .andReturn().getResponse().getContentAsString();

        String token = JsonPath.read(body, "$.accessToken");
        mvc.perform(get("/v1/me").header("Authorization", "Bearer " + token))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.actorId").value(principal))
                .andExpect(jsonPath("$.tenantId").value(tenant.toString()));
    }

    @Test
    void startRedirectsToTheProviderWithTheCallbackAndAnAlphanumericState() throws Exception {
        TenantId tenant = newTenant();
        Org org = connect(tenant, "wecom", null);

        Started started = start(tenant, org.connectionId());

        Map<String, String> query = query(started.location());
        assertThat(started.location().toString()).startsWith("https://open.weixin.qq.com/connect/oauth2/authorize?");
        assertThat(started.location().getFragment()).isEqualTo("wechat_redirect");
        assertThat(query).containsEntry("appid", org.corp()).containsEntry("agentid", org.appId())
                .containsEntry("response_type", "code")
                .containsEntry("redirect_uri", CALLBACK_BASE + callbackPath(tenant, org.connectionId()));
        // 企业微信要求 state 只含字母数字、不超过 128 字节。
        assertThat(started.state()).matches("[A-Za-z0-9]{32,128}");
        assertThat(started.browser().isHttpOnly()).isTrue();
        assertThat(started.browser().getSecure()).isTrue();
        assertThat(started.browser().getValue()).doesNotContain(started.state());
    }

    @Test
    void dingtalkAndFeishuAuthorizeUrlsFollowTheirOauthContracts() throws Exception {
        TenantId tenant = newTenant();
        Org ding = connect(tenant, "dingtalk", null);
        Org feishu = connect(tenant, "feishu", null);

        Started dingStart = start(tenant, ding.connectionId());
        Started feishuStart = start(tenant, feishu.connectionId());

        assertThat(dingStart.location().toString()).startsWith("https://login.dingtalk.com/oauth2/auth?");
        assertThat(query(dingStart.location())).containsEntry("client_id", ding.appId())
                .containsEntry("response_type", "code").containsEntry("scope", "openid corpid")
                .containsEntry("prompt", "consent").containsEntry("corpId", ding.corp());
        assertThat(feishuStart.location().toString())
                .startsWith("https://accounts.feishu.cn/open-apis/authen/v1/authorize?");
        assertThat(query(feishuStart.location())).containsEntry("client_id", feishu.appId())
                .containsEntry("response_type", "code");
    }

    @Test
    void aReplayedStateIsRejected() throws Exception {
        TenantId tenant = newTenant();
        Org org = connect(tenant, "feishu", null);
        String userId = unique("u");
        preauthorize(org, userId);
        Started started = start(tenant, org.connectionId());
        callback(tenant, org.connectionId(), FAKE.issueCode("feishu", org.corp(), org.appId(), userId), started)
                .andExpect(status().isOk());

        callback(tenant, org.connectionId(), FAKE.issueCode("feishu", org.corp(), org.appId(), userId), started)
                .andExpect(status().isBadRequest())
                .andExpect(jsonPath("$.error").value("invalid_login_state"));
    }

    @Test
    void aStateStartedForOneTenantIsRejectedOnAnotherTenantsCallback() throws Exception {
        TenantId tenantA = newTenant();
        TenantId tenantB = newTenant();
        Org orgA = connect(tenantA, "wecom", null);
        Org orgB = connect(tenantB, "wecom", null);
        String userId = unique("u");
        preauthorize(orgB, userId);
        Started startedInA = start(tenantA, orgA.connectionId());

        String code = FAKE.issueCode("wecom", orgB.corp(), orgB.appId(), userId);
        callback(tenantB, orgB.connectionId(), code, startedInA)
                .andExpect(status().isBadRequest())
                .andExpect(jsonPath("$.error").value("invalid_login_state"));
    }

    @Test
    void aStateIsBoundToTheConnectionItWasStartedFor() throws Exception {
        TenantId tenant = newTenant();
        Org first = connect(tenant, "wecom", null);
        Org second = connect(tenant, "wecom", null);
        String userId = unique("u");
        preauthorize(second, userId);
        Started started = start(tenant, first.connectionId());

        callback(tenant, second.connectionId(), FAKE.issueCode("wecom", second.corp(), second.appId(), userId), started)
                .andExpect(status().isBadRequest())
                .andExpect(jsonPath("$.error").value("invalid_login_state"));
    }

    @Test
    void anExpiredStateIsRejected() throws Exception {
        TenantId tenant = newTenant();
        Org org = connect(tenant, "dingtalk", null);
        String userId = unique("u");
        FAKE.addUser("dingtalk", org.corp(), userId);
        preauthorize(org, userId);
        Started started = start(tenant, org.connectionId());
        expireState(tenant, started.state());

        callback(tenant, org.connectionId(), FAKE.issueCode("dingtalk", org.corp(), org.appId(), userId), started)
                .andExpect(status().isBadRequest())
                .andExpect(jsonPath("$.error").value("invalid_login_state"));
    }

    /** 登录 CSRF：攻击者把自己的 state 与授权码塞给受害者的浏览器——没有发起时种下的 Cookie，就不能完成登录。 */
    @Test
    void aCallbackWithoutTheBrowserThatStartedTheLoginIsRejected() throws Exception {
        TenantId tenant = newTenant();
        Org org = connect(tenant, "wecom", null);
        String userId = unique("u");
        preauthorize(org, userId);
        Started started = start(tenant, org.connectionId());

        Started noCookie = new Started(started.state(), null, started.location());
        callback(tenant, org.connectionId(), FAKE.issueCode("wecom", org.corp(), org.appId(), userId), noCookie)
                .andExpect(status().isBadRequest())
                .andExpect(jsonPath("$.error").value("invalid_login_state"));
        Started otherBrowser = new Started(started.state(),
                new Cookie(OrgLoginController.BROWSER_COOKIE, "x".repeat(43)), started.location());
        callback(tenant, org.connectionId(), FAKE.issueCode("wecom", org.corp(), org.appId(), userId), otherBrowser)
                .andExpect(status().isBadRequest());
    }

    @ParameterizedTest
    @ValueSource(strings = {"wecom", "dingtalk", "feishu"})
    void anInvalidCodeIsRejectedAndDoesNotSignAnyoneIn(String provider) throws Exception {
        TenantId tenant = newTenant();
        Org org = connect(tenant, provider, null);
        Started started = start(tenant, org.connectionId());

        callback(tenant, org.connectionId(), "not-a-real-code", started)
                .andExpect(status().isUnauthorized())
                .andExpect(jsonPath("$.error").value("provider_rejected"))
                .andExpect(jsonPath("$.accessToken").doesNotExist());
    }

    @ParameterizedTest
    @ValueSource(strings = {"wecom", "dingtalk", "feishu"})
    void anExpiredCodeIsRejected(String provider) throws Exception {
        TenantId tenant = newTenant();
        Org org = connect(tenant, provider, null);
        String userId = unique("u");
        FAKE.addUser(provider, org.corp(), userId);
        preauthorize(org, userId);
        Started started = start(tenant, org.connectionId());
        String code = FAKE.issueCode(provider, org.corp(), org.appId(), userId);
        FAKE.expireCode(code);

        callback(tenant, org.connectionId(), code, started)
                .andExpect(status().isUnauthorized())
                .andExpect(jsonPath("$.error").value("provider_rejected"));
    }

    /** 钉钉、飞书的授权码可能来自用户在别的企业里的授权：返回的 corpId / tenant_key 与连接不符时拒绝。 */
    @ParameterizedTest
    @ValueSource(strings = {"dingtalk", "feishu"})
    void aUserAuthorizingFromAnotherOrganisationIsRejected(String provider) throws Exception {
        TenantId tenant = newTenant();
        Org org = connect(tenant, provider, "jit_staff");
        String userId = unique("u");
        Started started = start(tenant, org.connectionId());

        String code = FAKE.issueCode(provider, unique("othercorp"), org.appId(), userId);
        callback(tenant, org.connectionId(), code, started)
                .andExpect(status().isForbidden())
                .andExpect(jsonPath("$.error").value("wrong_organisation"));
    }

    /** 企业微信的授权码只能用本企业的 access_token 兑换：别的企业的码被开放平台拒绝（40029）。 */
    @Test
    void aWecomCodeFromAnotherCorpIsRejected() throws Exception {
        TenantId tenant = newTenant();
        Org org = connect(tenant, "wecom", "jit_staff");
        Started started = start(tenant, org.connectionId());

        callback(tenant, org.connectionId(), FAKE.issueCode("wecom", unique("othercorp"), org.appId(), "zhangsan"),
                started)
                .andExpect(status().isUnauthorized())
                .andExpect(jsonPath("$.error").value("provider_rejected"));
    }

    @Test
    void anUnknownOrgMemberIsRefusedByDefault() throws Exception {
        TenantId tenant = newTenant();
        Org org = connect(tenant, "wecom", null);
        Started started = start(tenant, org.connectionId());

        callback(tenant, org.connectionId(), FAKE.issueCode("wecom", org.corp(), org.appId(), unique("u")), started)
                .andExpect(status().isForbidden())
                .andExpect(jsonPath("$.error").value("not_authorized"));
    }

    @Test
    void theDefaultUnknownUserPolicyRequiresPreauthorisation() throws Exception {
        TenantId tenant = newTenant();
        Org org = connect(tenant, "feishu", null);

        mvc.perform(get("/v1/org-connections/" + org.connectionId()).header("Authorization", ownerBearer(tenant)))
                .andExpect(jsonPath("$.unknownUserPolicy").value("require_preauthorized"));
    }

    @Test
    void withJitProvisioningAnUnknownOrgMemberBecomesStaffAndTakesASeat() throws Exception {
        TenantId tenant = newTenant();
        Org org = connect(tenant, "dingtalk", "jit_staff");
        String userId = unique("u");
        FAKE.addUser("dingtalk", org.corp(), userId);
        long seatsBefore = members.seatsInUse(cn.mjy.platform.access.AccessFixture.context(tenant, OWNER));

        String token = login(org, userId);

        mvc.perform(get("/v1/me").header("Authorization", "Bearer " + token)).andExpect(status().isOk());
        assertThat(members.seatsInUse(cn.mjy.platform.access.AccessFixture.context(tenant, OWNER)))
                .isEqualTo(seatsBefore + 1);
    }

    @Test
    void jitProvisioningIsRefusedWhenNoSeatIsFree() throws Exception {
        TenantId tenant = newTenant();
        seats.setLimit(tenant, 1);
        Org org = connect(tenant, "feishu", "jit_staff");
        Started started = start(tenant, org.connectionId());

        callback(tenant, org.connectionId(), FAKE.issueCode("feishu", org.corp(), org.appId(), unique("u")), started)
                .andExpect(status().isForbidden())
                .andExpect(jsonPath("$.error").value("seat_limit"));
    }

    @Test
    void signingInTwiceResolvesToTheSamePrincipal() throws Exception {
        TenantId tenant = newTenant();
        Org org = connect(tenant, "wecom", "jit_staff");
        String userId = unique("u");

        String first = login(org, userId);
        String second = login(org, userId);

        assertThat(subject(second)).isEqualTo(subject(first));
    }

    /** 同一个用户标识字符串在两个企业（两个应用作用域）下是两个人，绝不合并。 */
    @Test
    void theSameUserIdUnderTwoOrganisationsIsTwoPrincipals() throws Exception {
        TenantId tenant = newTenant();
        Org first = connect(tenant, "feishu", "jit_staff");
        Org second = connect(tenant, "feishu", "jit_staff");
        Org dingtalk = connect(tenant, "dingtalk", "jit_staff");
        String userId = unique("u");
        FAKE.addUser("dingtalk", dingtalk.corp(), userId);

        String a = subject(login(first, userId));
        String b = subject(login(second, userId));
        String c = subject(login(dingtalk, userId));

        assertThat(a).isNotEqualTo(b).isNotEqualTo(c);
        assertThat(b).isNotEqualTo(c);
    }

    /** 同一个人在两个租户里是两个互不相关的主体；A 的令牌进不了 B。 */
    @Test
    void theSamePersonInTwoTenantsIsTwoUnrelatedPrincipals() throws Exception {
        TenantId tenantA = newTenant();
        TenantId tenantB = newTenant();
        String corp = unique("corp");
        Org inA = connect(tenantA, "wecom", corp, unique("agent"), "jit_staff");
        Org inB = connect(tenantB, "wecom", corp, inA.appId(), inA.secret(), "jit_staff");
        String userId = unique("u");

        String tokenA = login(inA, userId);
        String tokenB = login(inB, userId);

        assertThat(subject(tokenA)).isNotEqualTo(subject(tokenB));
        mvc.perform(get("/v1/identity-bindings/" + subject(tokenA)).header("Authorization", "Bearer " + tokenB))
                .andExpect(status().isNotFound());
    }

    @Test
    void anUnknownOrDisabledConnectionCannotStartALogin() throws Exception {
        TenantId tenant = newTenant();
        Org org = connect(tenant, "wecom", null);
        mvc.perform(get(startPath(tenant, UUID.randomUUID()))).andExpect(status().isNotFound());
        mvc.perform(get(startPath(newTenant(), org.connectionId()))).andExpect(status().isNotFound());

        disable(org);

        mvc.perform(get(startPath(tenant, org.connectionId()))).andExpect(status().isNotFound());
    }

    @Test
    void aProviderOutageIsReportedAsBadGateway() throws Exception {
        TenantId tenant = newTenant();
        Org org = connect(tenant, "dingtalk", "jit_staff");
        Started started = start(tenant, org.connectionId());
        String code = FAKE.issueCode("dingtalk", org.corp(), org.appId(), unique("u"));
        FAKE.failNextWith(503);

        callback(tenant, org.connectionId(), code, started)
                .andExpect(status().isBadGateway())
                .andExpect(jsonPath("$.error").value("provider_unavailable"));
    }

    @Test
    void aMissingSecretFailsClosed() throws Exception {
        TenantId tenant = newTenant();
        createConnection(tenant, "feishu", unique("corp"), unique("app"), "NOT_PROVISIONED", "jit_staff")
                .andExpect(status().isCreated());
        String id = JsonPath.read(mvc.perform(get("/v1/org-connections").header("Authorization", ownerBearer(tenant)))
                .andReturn().getResponse().getContentAsString(), "$[0].id");
        Started started = start(tenant, UUID.fromString(id));

        callback(tenant, UUID.fromString(id), "code", started)
                .andExpect(status().isServiceUnavailable())
                .andExpect(jsonPath("$.error").value("not_configured"));
    }
}
