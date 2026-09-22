package cn.mjy.platform.identity.org;

import static org.assertj.core.api.Assertions.assertThat;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

import cn.mjy.platform.access.AccessFixture;
import cn.mjy.platform.access.MemberKind;
import cn.mjy.platform.shared.TenantContext;
import cn.mjy.platform.shared.TenantId;
import org.junit.jupiter.api.Test;
import org.springframework.test.web.servlet.ResultActions;

/**
 * 撤销后恢复（复职 / 误判）：管理员（租户级 manage-members）恢复后，此人按员工被重新邀请，下次免登即恢复访问；
 * 旧令牌不复活；撤销历史保留，再次离职会再次撤销。无权者 403、未撤销 409、别的租户 404、没有席位 409 且不改任何东西。
 */
class OrgReinstatementTest extends OrgLoginTestSupport {

    private record Departed(Org org, String userId, String principal, String oldToken) {
    }

    /** 免登一次后被同步判定离职、撤销。 */
    private Departed departed() throws Exception {
        TenantId tenant = newTenant();
        Org org = connect(tenant, "wecom", "jit_staff");
        String userId = unique("u");
        FAKE.addUser("wecom", org.corp(), userId);
        String token = login(org, userId);
        FAKE.setStatus("wecom", org.corp(), userId, FakeOrgPlatforms.Status.LEFT);
        sync(org).andExpect(jsonPath("$.revoked").value(1));
        return new Departed(org, userId, subject(token), token);
    }

    private ResultActions sync(Org org) throws Exception {
        return mvc.perform(post("/v1/org-connections/" + org.connectionId() + "/sync")
                .header("Authorization", ownerBearer(org.tenant())));
    }

    private ResultActions reinstate(TenantId tenant, String bearer, String principal) throws Exception {
        return mvc.perform(post("/v1/identity-bindings/" + principal + "/reinstatement")
                .header("Authorization", bearer));
    }

    private ResultActions signIn(Org org, String userId) throws Exception {
        Started started = start(org.tenant(), org.connectionId());
        return callback(org.tenant(), org.connectionId(), FAKE.issueCode("wecom", org.corp(), org.appId(), userId),
                started);
    }

    @Test
    void aReinstatedMemberCanSignInAgainButOldTokensStayDead() throws Exception {
        Departed d = departed();
        signIn(d.org(), d.userId()).andExpect(status().isForbidden());
        FAKE.setStatus("wecom", d.org().corp(), d.userId(), FakeOrgPlatforms.Status.ACTIVE);

        reinstate(d.org().tenant(), ownerBearer(d.org().tenant()), d.principal())
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.principalId").value(d.principal()))
                .andExpect(jsonPath("$.externalId").value(d.userId()));

        String fresh = com.jayway.jsonpath.JsonPath.read(signIn(d.org(), d.userId())
                .andExpect(status().isOk()).andReturn().getResponse().getContentAsString(), "$.accessToken");
        assertThat(subject(fresh)).isEqualTo(d.principal());
        mvc.perform(get("/v1/me").header("Authorization", "Bearer " + fresh)).andExpect(status().isOk());
        mvc.perform(get("/v1/me").header("Authorization", "Bearer " + d.oldToken()))
                .andExpect(status().isUnauthorized());
        assertThat(tenantScope.call(d.org().tenant(), () -> jdbc
                .sql("SELECT COUNT(*) FROM audit_log WHERE action = 'identity.binding.reinstate' AND resource LIKE :r")
                .param("r", "principal/" + d.principal() + "%").query(Long.class).single())).isEqualTo(1);
    }

    @Test
    void aReinstatedMemberWhoLeavesAgainIsRevokedAgain() throws Exception {
        Departed d = departed();
        reinstate(d.org().tenant(), ownerBearer(d.org().tenant()), d.principal()).andExpect(status().isOk());
        FAKE.setStatus("wecom", d.org().corp(), d.userId(), FakeOrgPlatforms.Status.ACTIVE);
        signIn(d.org(), d.userId()).andExpect(status().isOk());

        FAKE.setStatus("wecom", d.org().corp(), d.userId(), FakeOrgPlatforms.Status.LEFT);
        sync(d.org()).andExpect(jsonPath("$.revoked").value(1));

        signIn(d.org(), d.userId()).andExpect(status().isForbidden());
    }

    @Test
    void reinstatementRequiresTenantLevelManageMembers() throws Exception {
        Departed d = departed();
        TenantId tenant = d.org().tenant();
        TenantContext owner = AccessFixture.context(tenant, OWNER);
        members.invite(owner, "plain", MemberKind.STAFF);
        members.acceptInvitation(AccessFixture.context(tenant, "plain"));

        reinstate(tenant, bearer(tenant, "plain"), d.principal()).andExpect(status().isForbidden());
        reinstate(tenant, bearer(tenant, "stranger"), d.principal()).andExpect(status().isForbidden());
        reinstate(tenant, "Bearer " + d.oldToken(), d.principal()).andExpect(status().isUnauthorized());
        signIn(d.org(), d.userId()).andExpect(status().isForbidden());
    }

    @Test
    void onlyARevokedBindingInTheCallersTenantCanBeReinstated() throws Exception {
        Departed d = departed();
        TenantId other = newTenant();
        Org org = connect(other, "wecom", "jit_staff");
        String active = subject(login(org, unique("u")));

        reinstate(other, ownerBearer(other), d.principal()).andExpect(status().isNotFound());
        reinstate(other, ownerBearer(other), active).andExpect(status().isConflict());
        reinstate(d.org().tenant(), ownerBearer(d.org().tenant()), d.principal()).andExpect(status().isOk());
        reinstate(d.org().tenant(), ownerBearer(d.org().tenant()), d.principal()).andExpect(status().isConflict());
    }

    @Test
    void withoutAFreeSeatNothingIsReinstated() throws Exception {
        Departed d = departed();
        TenantId tenant = d.org().tenant();
        seats.setLimit(tenant, (int) members.seatsInUse(AccessFixture.context(tenant, OWNER)));

        reinstate(tenant, ownerBearer(tenant), d.principal()).andExpect(status().isConflict());

        FAKE.setStatus("wecom", d.org().corp(), d.userId(), FakeOrgPlatforms.Status.ACTIVE);
        signIn(d.org(), d.userId()).andExpect(status().isForbidden());
        assertThat(tenantScope.call(tenant, () -> jdbc
                .sql("SELECT COUNT(*) FROM identity_binding_revocation WHERE reinstated_at IS NULL")
                .query(Long.class).single())).isEqualTo(1);
    }
}
