package cn.mjy.platform.access;

import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

import cn.mjy.platform.shared.TenantContext;
import cn.mjy.platform.support.FakeSeatAllowance;
import cn.mjy.platform.support.TestTokens;
import java.util.List;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.webmvc.test.autoconfigure.AutoConfigureMockMvc;
import org.springframework.http.MediaType;
import org.springframework.test.web.servlet.MockMvc;

@SpringBootTest
@AutoConfigureMockMvc
class MemberManagementApiTest {
    @Autowired MockMvc mvc;
    @Autowired AccessFixture fixture;
    @Autowired TestTokens tokens;
    @Autowired FakeSeatAllowance seats;
    TenantContext owner;
    TenantContext other;

    @BeforeEach void setup() {
        owner = fixture.newTenant();
        other = fixture.newTenant();
        seats.setLimit(owner.tenantId(), 2);
    }
    private String bearer(TenantContext ctx) {
        return "Bearer " + tokens.issue(ctx.actorId(), ctx.tenantId().value(), List.of());
    }
    private void invite(TenantContext ctx, String actor, int expected) throws Exception {
        mvc.perform(post("/v1/members").header("Authorization", bearer(ctx)).contentType(MediaType.APPLICATION_JSON)
                .content("{\"actorId\":\"" + actor + "\",\"kind\":\"STAFF\"}"))
                .andExpect(status().is(expected));
    }
    @Test void ownerManagesMembersWithoutCrossTenantLeakAndCannotRemoveLastOwner() throws Exception {
        invite(owner, "member-a", 201);
        mvc.perform(get("/v1/members").header("Authorization", bearer(owner)))
                .andExpect(status().isOk()).andExpect(jsonPath("$.items.length()").value(2));
        mvc.perform(get("/v1/members").header("Authorization", bearer(other)))
                .andExpect(status().isOk()).andExpect(jsonPath("$.items.length()").value(1));
        invite(owner, "over-limit", 409);
        mvc.perform(post("/v1/members/remove").header("Authorization", bearer(owner)).contentType(MediaType.APPLICATION_JSON)
                .content("{\"actorId\":\"owner\"}")).andExpect(status().isConflict());
        mvc.perform(post("/v1/members/remove").header("Authorization", bearer(owner)).contentType(MediaType.APPLICATION_JSON)
                .content("{\"actorId\":\"member-a\"}")).andExpect(status().isNoContent());
        invite(owner, "replacement", 201);
    }
    @Test void memberPaginationIsBoundedAndCapabilitiesAreServerDerived() throws Exception {
        invite(owner, "member-a", 201);
        mvc.perform(get("/v1/members").param("limit", "1").header("Authorization", bearer(owner)))
                .andExpect(status().isOk()).andExpect(jsonPath("$.items.length()").value(1))
                .andExpect(jsonPath("$.nextCursor").value("member-a"));
        mvc.perform(get("/v1/members").param("limit", "1").param("after", "member-a").header("Authorization", bearer(owner)))
                .andExpect(status().isOk()).andExpect(jsonPath("$.items[0].actorId").value("owner"))
                .andExpect(jsonPath("$.nextCursor").doesNotExist());
        mvc.perform(get("/v1/members").param("limit", "101").header("Authorization", bearer(owner)))
                .andExpect(status().isBadRequest());
        mvc.perform(get("/v1/members/capabilities").header("Authorization", bearer(owner)))
                .andExpect(status().isOk()).andExpect(jsonPath("$.canManageMembers").value(true));
        TenantContext viewer = fixture.activeMember(other, "viewer");
        mvc.perform(get("/v1/members/capabilities").header("Authorization", bearer(viewer)))
                .andExpect(status().isOk()).andExpect(jsonPath("$.canManageMembers").value(false));
    }
    @Test void managerCanGrantAndRevokeWithoutBypassingDelegation() throws Exception {
        seats.setLimit(owner.tenantId(), 5);
        fixture.activeMember(owner, "employee");
        String body = mvc.perform(post("/v1/member-grants").header("Authorization", bearer(owner))
                .contentType(MediaType.APPLICATION_JSON)
                .content("{\"actorId\":\"employee\",\"roleCode\":\"editor\",\"resourceId\":null,\"expiresAt\":null}"))
                .andExpect(status().isCreated()).andReturn().getResponse().getContentAsString();
        String id = com.jayway.jsonpath.JsonPath.read(body, "$.id");
        mvc.perform(get("/v1/member-grants").header("Authorization", bearer(owner)))
                .andExpect(status().isOk()).andExpect(jsonPath("$.items[?(@.actorId == 'employee')].roleCode").value("editor"));
        mvc.perform(get("/v1/member-grants").param("limit", "1").header("Authorization", bearer(owner)))
                .andExpect(status().isOk()).andExpect(jsonPath("$.items.length()").value(1))
                .andExpect(jsonPath("$.nextCursor").isNotEmpty());
        mvc.perform(get("/v1/member-grants").param("limit", "101").header("Authorization", bearer(owner)))
                .andExpect(status().isBadRequest());
        mvc.perform(post("/v1/member-grants/" + id + "/revoke").header("Authorization", bearer(owner)))
                .andExpect(status().isNoContent());
        TenantContext admin = fixture.member(owner, "admin", "org_admin", null);
        mvc.perform(post("/v1/member-grants").header("Authorization", bearer(admin)).contentType(MediaType.APPLICATION_JSON)
                .content("{\"actorId\":\"employee\",\"roleCode\":\"tenant_owner\",\"resourceId\":null,\"expiresAt\":null}"))
                .andExpect(status().isForbidden());
    }
    @Test void memberWithoutManageMembersAndAnonymousAreDenied() throws Exception {
        TenantContext viewer = fixture.activeMember(other, "viewer");
        mvc.perform(get("/v1/members").header("Authorization", bearer(viewer))).andExpect(status().isForbidden());
        invite(viewer, "target", 403);
        mvc.perform(get("/v1/members")).andExpect(status().isUnauthorized());
    }
}
