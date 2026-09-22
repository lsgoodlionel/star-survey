package cn.mjy.platform.identity.org;

import static org.assertj.core.api.Assertions.assertThat;
import static org.hamcrest.Matchers.hasSize;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.put;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

import cn.mjy.platform.shared.TenantId;
import java.util.List;
import java.util.UUID;
import org.junit.jupiter.api.Test;
import org.springframework.http.MediaType;

/** 组织连接的管理：只有租户级 manage-settings 能建与改；别的租户看不到、改不了；响应里没有密钥。 */
class OrgConnectionApiTest extends OrgLoginTestSupport {

    @Test
    void aTenantAdminCreatesAConnectionAndItIsAudited() throws Exception {
        TenantId tenant = newTenant();
        String corp = unique("ww");

        createConnection(tenant, "wecom", corp, "1000002", "WECOM_APP", "jit_staff")
                .andExpect(status().isCreated())
                .andExpect(jsonPath("$.provider").value("wecom"))
                .andExpect(jsonPath("$.corpId").value(corp))
                .andExpect(jsonPath("$.appId").value("1000002"))
                .andExpect(jsonPath("$.secretRef").value("WECOM_APP"))
                .andExpect(jsonPath("$.unknownUserPolicy").value("jit_staff"))
                .andExpect(jsonPath("$.enabled").value(true));

        List<String> actions = tenantScope.call(tenant, () -> jdbc
                .sql("SELECT action FROM audit_log WHERE action LIKE 'identity.org_connection.%'")
                .query(String.class).list());
        assertThat(actions).containsExactly("identity.org_connection.create");
    }

    @Test
    void creatingAConnectionRequiresManageSettings() throws Exception {
        TenantId tenant = newTenant();

        mvc.perform(post("/v1/org-connections")
                        .header("Authorization", bearer(tenant, "not-a-member"))
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"provider\":\"wecom\",\"corpId\":\"ww1\",\"appId\":\"1\",\"secretRef\":\"X\"}"))
                .andExpect(status().isForbidden());
    }

    @Test
    void theSameAppCannotBeConnectedTwiceInOneTenant() throws Exception {
        TenantId tenant = newTenant();
        createConnection(tenant, "feishu", "tk1", "cli_1", "FEISHU", null).andExpect(status().isCreated());

        createConnection(tenant, "feishu", "tk1", "cli_1", "FEISHU", null).andExpect(status().isConflict());
    }

    @Test
    void connectionRequestsAreValidated() throws Exception {
        TenantId tenant = newTenant();
        createConnection(tenant, "wechat_mp", "c", "a", "REF", null).andExpect(status().isBadRequest());
        createConnection(tenant, "wecom", "", "a", "REF", null).andExpect(status().isBadRequest());
        createConnection(tenant, "wecom", "c/../x", "a", "REF", null).andExpect(status().isBadRequest());
        createConnection(tenant, "wecom", "c", "a", "lower_case", null).andExpect(status().isBadRequest());
        createConnection(tenant, "wecom", "c", "a", "REF", "auto_admin").andExpect(status().isBadRequest());
        // 密钥只能以名字引用：明文密钥字段不被接受。
        mvc.perform(post("/v1/org-connections")
                        .header("Authorization", ownerBearer(tenant))
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"provider\":\"wecom\",\"corpId\":\"c\",\"appId\":\"a\",\"secret\":\"plain\"}"))
                .andExpect(status().isBadRequest());
    }

    @Test
    void anotherTenantCannotSeeOrChangeTheConnection() throws Exception {
        TenantId tenantA = newTenant();
        TenantId tenantB = newTenant();
        Org org = connect(tenantA, "dingtalk", null);

        mvc.perform(get("/v1/org-connections/" + org.connectionId()).header("Authorization", ownerBearer(tenantB)))
                .andExpect(status().isNotFound());
        mvc.perform(get("/v1/org-connections").header("Authorization", ownerBearer(tenantB)))
                .andExpect(jsonPath("$", hasSize(0)));
        mvc.perform(put("/v1/org-connections/" + org.connectionId())
                        .header("Authorization", ownerBearer(tenantB))
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"enabled\":false}"))
                .andExpect(status().isNotFound());
        mvc.perform(post("/v1/org-connections/" + org.connectionId() + "/authorized-users")
                        .header("Authorization", ownerBearer(tenantB))
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"externalId\":\"u1\"}"))
                .andExpect(status().isNotFound());
        mvc.perform(get("/v1/org-connections/" + org.connectionId()).header("Authorization", ownerBearer(tenantA)))
                .andExpect(jsonPath("$.enabled").value(true));
    }

    @Test
    void anAdminUpdatesThePolicyAndDisablesTheConnection() throws Exception {
        TenantId tenant = newTenant();
        Org org = connect(tenant, "feishu", null);

        mvc.perform(put("/v1/org-connections/" + org.connectionId())
                        .header("Authorization", ownerBearer(tenant))
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"unknownUserPolicy\":\"jit_staff\",\"enabled\":false}"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.unknownUserPolicy").value("jit_staff"))
                .andExpect(jsonPath("$.enabled").value(false));
        mvc.perform(put("/v1/org-connections/" + UUID.randomUUID())
                        .header("Authorization", ownerBearer(tenant))
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"enabled\":false}"))
                .andExpect(status().isNotFound());
    }

    @Test
    void preauthorisingAUserRequiresManageMembersAndIsIdempotent() throws Exception {
        TenantId tenant = newTenant();
        Org org = connect(tenant, "wecom", null);

        String first = preauthorize(org, "zhangsan");
        mvc.perform(post("/v1/org-connections/" + org.connectionId() + "/authorized-users")
                        .header("Authorization", ownerBearer(tenant))
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"externalId\":\"zhangsan\"}"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.principalId").value(first));
        mvc.perform(post("/v1/org-connections/" + org.connectionId() + "/authorized-users")
                        .header("Authorization", bearer(tenant, "stranger"))
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"externalId\":\"lisi\"}"))
                .andExpect(status().isForbidden());
    }

    /** 同一个密钥名在别的租户下解析不到本租户的密钥：B 抄 A 的企业与密钥名，也拿不到 A 的密钥。 */
    @Test
    void aSecretNameCannotBeBorrowedAcrossTenants() throws Exception {
        TenantId tenantA = newTenant();
        TenantId tenantB = newTenant();
        Org orgA = connect(tenantA, "feishu", "jit_staff");
        createConnection(tenantB, "feishu", orgA.corp(), orgA.appId(), orgA.secretRef(), "jit_staff")
                .andExpect(status().isCreated());
        String idB = com.jayway.jsonpath.JsonPath.read(mvc.perform(get("/v1/org-connections")
                .header("Authorization", ownerBearer(tenantB))).andReturn().getResponse().getContentAsString(), "$[0].id");
        Started started = start(tenantB, UUID.fromString(idB));

        callback(tenantB, UUID.fromString(idB), FAKE.issueCode("feishu", orgA.corp(), orgA.appId(), "u1"), started)
                .andExpect(status().isServiceUnavailable())
                .andExpect(jsonPath("$.error").value("not_configured"));
    }

    @Test
    void connectionEndpointsRequireAToken() throws Exception {
        mvc.perform(get("/v1/org-connections")).andExpect(status().isUnauthorized());
    }
}
