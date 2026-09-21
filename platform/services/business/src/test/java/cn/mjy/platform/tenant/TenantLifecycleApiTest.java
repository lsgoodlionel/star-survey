package cn.mjy.platform.tenant;

import static org.assertj.core.api.Assertions.assertThat;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

import cn.mjy.platform.shared.TenantId;
import cn.mjy.platform.shared.tenant.TenantScope;
import com.jayway.jsonpath.JsonPath;
import java.util.List;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.webmvc.test.autoconfigure.AutoConfigureMockMvc;
import org.springframework.http.MediaType;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.test.web.servlet.ResultActions;

/** 租户开通与状态变更是控制平面操作：只有平台运营可以做，且每次变更都留审计。 */
@SpringBootTest
@AutoConfigureMockMvc
class TenantLifecycleApiTest {

    private static final String TENANTS = "/v1/platform/tenants";

    @Autowired
    private MockMvc mvc;

    @Autowired
    private TenantFixtures fixtures;

    @Autowired
    private TenantScope tenantScope;

    @Autowired
    private JdbcClient jdbc;

    private ResultActions createTenant(String bearer, String code, String name) throws Exception {
        return mvc.perform(post(TENANTS)
                .header("Authorization", bearer)
                .contentType(MediaType.APPLICATION_JSON)
                .content("{\"code\":\"%s\",\"name\":\"%s\"}".formatted(code, name)));
    }

    private ResultActions changeStatus(String bearer, String tenantId, String target) throws Exception {
        return mvc.perform(post(TENANTS + "/" + tenantId + "/status")
                .header("Authorization", bearer)
                .contentType(MediaType.APPLICATION_JSON)
                .content("{\"status\":\"%s\"}".formatted(target)));
    }

    private String createdTenantId() throws Exception {
        String body = createTenant(fixtures.operatorBearer(), TenantFixtures.uniqueCode("acme"), "Acme")
                .andExpect(status().isCreated())
                .andReturn().getResponse().getContentAsString();
        return JsonPath.read(body, "$.id");
    }

    private List<String> auditActions(String tenantId) {
        return tenantScope.call(TenantId.of(tenantId), () -> jdbc
                .sql("SELECT action FROM audit_log ORDER BY id").query(String.class).list());
    }

    @Test
    void anOperatorCreatesATenantInProvisioningAndTheCreationIsAudited() throws Exception {
        String code = TenantFixtures.uniqueCode("acme");
        String body = createTenant(fixtures.operatorBearer(), code, "Acme 公司")
                .andExpect(status().isCreated())
                .andExpect(jsonPath("$.code").value(code))
                .andExpect(jsonPath("$.name").value("Acme 公司"))
                .andExpect(jsonPath("$.status").value("provisioning"))
                .andReturn().getResponse().getContentAsString();
        String id = JsonPath.read(body, "$.id");

        mvc.perform(get(TENANTS + "/" + id).header("Authorization", fixtures.operatorBearer()))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.status").value("provisioning"));
        assertThat(auditActions(id)).containsExactly("tenant.create");
    }

    @Test
    void theWholeLifecycleWorksAndEveryStateChangeIsAudited() throws Exception {
        String id = createdTenantId();
        String operator = fixtures.operatorBearer();

        changeStatus(operator, id, "active").andExpect(status().isOk()).andExpect(jsonPath("$.status").value("active"));
        changeStatus(operator, id, "suspended").andExpect(status().isOk()).andExpect(jsonPath("$.status").value("suspended"));
        changeStatus(operator, id, "active").andExpect(status().isOk()).andExpect(jsonPath("$.status").value("active"));
        changeStatus(operator, id, "closed").andExpect(status().isOk()).andExpect(jsonPath("$.status").value("closed"));

        assertThat(auditActions(id)).containsExactly(
                "tenant.create",
                "tenant.status:provisioning->active",
                "tenant.status:active->suspended",
                "tenant.status:suspended->active",
                "tenant.status:active->closed");
    }

    @Test
    void anIllegalTransitionIsRejectedAndLeavesNoAuditRecord() throws Exception {
        String id = createdTenantId();

        changeStatus(fixtures.operatorBearer(), id, "suspended")
                .andExpect(status().isConflict())
                .andExpect(jsonPath("$.error").value("illegal_transition"))
                .andExpect(jsonPath("$.message").value(org.hamcrest.Matchers.containsString("provisioning")));

        mvc.perform(get(TENANTS + "/" + id).header("Authorization", fixtures.operatorBearer()))
                .andExpect(jsonPath("$.status").value("provisioning"));
        assertThat(auditActions(id)).containsExactly("tenant.create");
    }

    @Test
    void aClosedTenantCannotBeReopened() throws Exception {
        String id = createdTenantId();
        changeStatus(fixtures.operatorBearer(), id, "closed").andExpect(status().isOk());

        for (String target : List.of("active", "suspended", "provisioning", "closed")) {
            changeStatus(fixtures.operatorBearer(), id, target).andExpect(status().isConflict());
        }
    }

    @Test
    void unknownStatusesAndUnknownTenantsAreClientErrors() throws Exception {
        String id = createdTenantId();

        changeStatus(fixtures.operatorBearer(), id, "deleted").andExpect(status().isBadRequest());
        changeStatus(fixtures.operatorBearer(), TenantId.random().toString(), "active").andExpect(status().isNotFound());
        mvc.perform(get(TENANTS + "/" + TenantId.random()).header("Authorization", fixtures.operatorBearer()))
                .andExpect(status().isNotFound());
        mvc.perform(get(TENANTS + "/not-a-uuid").header("Authorization", fixtures.operatorBearer()))
                .andExpect(status().isBadRequest());
    }

    @Test
    void creationRequestsAreValidated() throws Exception {
        createTenant(fixtures.operatorBearer(), "", "Acme").andExpect(status().isBadRequest());
        createTenant(fixtures.operatorBearer(), "Has Spaces", "Acme").andExpect(status().isBadRequest());
        createTenant(fixtures.operatorBearer(), TenantFixtures.uniqueCode("acme"), " ").andExpect(status().isBadRequest());
    }

    @Test
    void tenantCodesAreUnique() throws Exception {
        String code = TenantFixtures.uniqueCode("dup");
        createTenant(fixtures.operatorBearer(), code, "A").andExpect(status().isCreated());

        createTenant(fixtures.operatorBearer(), code, "B")
                .andExpect(status().isConflict())
                .andExpect(jsonPath("$.error").value("conflict"));
    }

    @Test
    void aNormalTenantTokenIsForbiddenOnEveryOperatorEndpoint() throws Exception {
        String id = createdTenantId();
        String user = fixtures.userBearer(TenantId.of(id));

        createTenant(user, TenantFixtures.uniqueCode("x"), "X").andExpect(status().isForbidden());
        changeStatus(user, id, "active").andExpect(status().isForbidden());
        mvc.perform(get(TENANTS + "/" + id).header("Authorization", user)).andExpect(status().isForbidden());
    }

    @Test
    void operatorEndpointsRequireAToken() throws Exception {
        String id = createdTenantId();

        mvc.perform(post(TENANTS).contentType(MediaType.APPLICATION_JSON).content("{\"code\":\"x-1\",\"name\":\"X\"}"))
                .andExpect(status().isUnauthorized());
        mvc.perform(post(TENANTS + "/" + id + "/status").contentType(MediaType.APPLICATION_JSON)
                        .content("{\"status\":\"active\"}"))
                .andExpect(status().isUnauthorized());
        mvc.perform(get(TENANTS + "/" + id)).andExpect(status().isUnauthorized());
    }
}
