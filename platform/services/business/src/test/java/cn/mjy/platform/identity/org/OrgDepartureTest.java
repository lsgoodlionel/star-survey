package cn.mjy.platform.identity.org;

import static org.assertj.core.api.Assertions.assertThat;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

import cn.mjy.platform.access.AccessFixture;
import cn.mjy.platform.shared.TenantId;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.CsvSource;
import org.junit.jupiter.params.provider.ValueSource;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;

/**
 * 离职后立即失权（R18-01、R20-01～03）：组织同步发现成员离职或被禁用后，绑定被撤销、已签发的令牌立即失效、
 * 不能再免登、成员移出并释放席位。只有开放平台给出确定结论时才撤销；查询失败不撤销。
 */
class OrgDepartureTest extends OrgLoginTestSupport {

    @Autowired
    private OrgDirectorySyncService sync;

    @ParameterizedTest
    @CsvSource({"wecom,LEFT", "wecom,DISABLED", "wecom,DELETED", "dingtalk,LEFT", "dingtalk,DELETED",
            "feishu,LEFT", "feishu,DISABLED"})
    void aDepartedMemberLosesAccessImmediately(String provider, FakeOrgPlatforms.Status departure) throws Exception {
        TenantId tenant = newTenant();
        Org org = connect(tenant, provider, "jit_staff");
        String userId = unique("u");
        FAKE.addUser(provider, org.corp(), userId);
        String token = login(org, userId);
        mvc.perform(get("/v1/me").header("Authorization", "Bearer " + token)).andExpect(status().isOk());

        FAKE.setStatus(provider, org.corp(), userId, departure);
        runSync(org).andExpect(status().isOk())
                .andExpect(jsonPath("$.revoked").value(1))
                .andExpect(jsonPath("$.unknown").value(0));

        mvc.perform(get("/v1/me").header("Authorization", "Bearer " + token)).andExpect(status().isUnauthorized());
        Started started = start(tenant, org.connectionId());
        callback(tenant, org.connectionId(), FAKE.issueCode(provider, org.corp(), org.appId(), userId), started)
                .andExpect(status().isForbidden())
                .andExpect(jsonPath("$.error").value("not_authorized"));
    }

    @Test
    void departureRemovesTheMemberAndFreesTheSeat() throws Exception {
        TenantId tenant = newTenant();
        Org org = connect(tenant, "wecom", "jit_staff");
        String userId = unique("u");
        FAKE.addUser("wecom", org.corp(), userId);
        login(org, userId);
        long seats = members.seatsInUse(AccessFixture.context(tenant, OWNER));

        FAKE.setStatus("wecom", org.corp(), userId, FakeOrgPlatforms.Status.LEFT);
        runSync(org).andExpect(status().isOk());

        assertThat(members.seatsInUse(AccessFixture.context(tenant, OWNER))).isEqualTo(seats - 1);
    }

    @Test
    void activeMembersAreLeftAloneAndSyncIsIdempotent() throws Exception {
        TenantId tenant = newTenant();
        Org org = connect(tenant, "feishu", "jit_staff");
        String stays = unique("u");
        String leaves = unique("u");
        FAKE.addUser("feishu", org.corp(), stays);
        FAKE.addUser("feishu", org.corp(), leaves);
        String stayingToken = login(org, stays);
        login(org, leaves);
        FAKE.setStatus("feishu", org.corp(), leaves, FakeOrgPlatforms.Status.LEFT);

        runSync(org).andExpect(jsonPath("$.checked").value(2)).andExpect(jsonPath("$.revoked").value(1));
        runSync(org).andExpect(jsonPath("$.checked").value(1)).andExpect(jsonPath("$.revoked").value(0));

        mvc.perform(get("/v1/me").header("Authorization", "Bearer " + stayingToken)).andExpect(status().isOk());
    }

    /** 开放平台查询失败或返回无法判定的结果时不撤销（避免一次故障把全员踢下线），计入 unknown。 */
    @ParameterizedTest
    @ValueSource(strings = {"wecom", "dingtalk", "feishu"})
    void anInconclusiveLookupDoesNotRevoke(String provider) throws Exception {
        TenantId tenant = newTenant();
        Org org = connect(tenant, provider, "jit_staff");
        String userId = unique("u");
        FAKE.addUser(provider, org.corp(), userId);
        String token = login(org, userId);

        FAKE.failNextWith(500);
        runSync(org).andExpect(status().isOk())
                .andExpect(jsonPath("$.revoked").value(0))
                .andExpect(jsonPath("$.unknown").value(1));

        mvc.perform(get("/v1/me").header("Authorization", "Bearer " + token)).andExpect(status().isOk());
    }

    /** 事件推送（通讯录回调）接入后走同一入口：报告离职即撤销。 */
    @Test
    void aReportedDepartureRevokesImmediately() throws Exception {
        TenantId tenant = newTenant();
        Org org = connect(tenant, "dingtalk", "jit_staff");
        String userId = unique("u");
        FAKE.addUser("dingtalk", org.corp(), userId);
        String token = login(org, userId);

        boolean revoked = sync.memberDeparted(tenant, org.connectionId(), userId, "trace-webhook");

        assertThat(revoked).isTrue();
        assertThat(sync.memberDeparted(tenant, org.connectionId(), userId, "trace-webhook")).isFalse();
        mvc.perform(get("/v1/me").header("Authorization", "Bearer " + token)).andExpect(status().isUnauthorized());
    }

    @Test
    void syncRequiresManageMembers() throws Exception {
        TenantId tenant = newTenant();
        Org org = connect(tenant, "wecom", null);

        mvc.perform(post("/v1/org-connections/" + org.connectionId() + "/sync")
                        .header("Authorization", bearer(tenant, "stranger")))
                .andExpect(status().isForbidden());
    }

    @Test
    void logoutRevokesTheSessionToken() throws Exception {
        TenantId tenant = newTenant();
        Org org = connect(tenant, "feishu", "jit_staff");
        String userId = unique("u");
        String token = login(org, userId);
        String other = login(org, userId);

        mvc.perform(post("/v1/auth/logout").header("Authorization", "Bearer " + token))
                .andExpect(status().isNoContent());

        mvc.perform(get("/v1/me").header("Authorization", "Bearer " + token)).andExpect(status().isUnauthorized());
        mvc.perform(get("/v1/me").header("Authorization", "Bearer " + other)).andExpect(status().isOk());
    }

    @Test
    void aSessionTokenForgedIntoAnotherTenantIsRejected() throws Exception {
        TenantId tenantA = newTenant();
        TenantId tenantB = newTenant();
        Org org = connect(tenantA, "wecom", "jit_staff");
        String token = login(org, unique("u"));

        // 同一 sid、同一主体，但租户声明换成 B（用平台密钥重签，模拟密钥之外的全部篡改面）。
        String forged = tokens.issueWithClaims(subject(token), tenantB.value(), java.util.Map.of("sid", sid(token)));

        mvc.perform(get("/v1/me").header("Authorization", "Bearer " + forged)).andExpect(status().isUnauthorized());
    }

    private org.springframework.test.web.servlet.ResultActions runSync(Org org) throws Exception {
        return mvc.perform(post("/v1/org-connections/" + org.connectionId() + "/sync")
                .header("Authorization", ownerBearer(org.tenant())));
    }

    private static String sid(String token) {
        String payload = new String(java.util.Base64.getUrlDecoder().decode(token.split("\\.")[1]),
                java.nio.charset.StandardCharsets.UTF_8);
        return com.jayway.jsonpath.JsonPath.read(payload, "$.sid");
    }
}
