package cn.mjy.platform.platformadmin;

import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

import cn.mjy.platform.entitlement.PlanCatalog;
import cn.mjy.platform.entitlement.PlanDefinition;
import cn.mjy.platform.shared.tenant.TenantScope;
import cn.mjy.platform.support.TestTokens;
import cn.mjy.platform.tenant.TenantFixtures;
import java.sql.DriverManager;
import java.util.Map;
import java.util.Set;
import java.util.UUID;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.webmvc.test.autoconfigure.AutoConfigureMockMvc;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.test.web.servlet.MockMvc;

@SpringBootTest(properties = {"platform.admin.enforce-personnel=true", "PLATFORM_PROBE_HMAC_SECRET=isolated-probe-key-for-tests-only-000000000000", "PLATFORM_PROBE_INSTALLATION_ID=10000000-0000-4000-8000-000000000001", "PLATFORM_ENGINE_EVENTS_SECRET=isolated-events-key-for-tests-only-00000000000"})
@AutoConfigureMockMvc
class PlatformPlanCatalogHttpTest {
    @Autowired MockMvc mvc; @Autowired TenantFixtures fixtures; @Autowired TenantScope scope;
    @Autowired JdbcClient jdbc; @Autowired TestTokens tokens; @Autowired PlanCatalog plans;
    @Value("${spring.flyway.url}") String url; @Value("${spring.flyway.user}") String user; @Value("${spring.flyway.password}") String password;

    @Test void catalogRequiresLivePersonnelGrantAndNeverAcceptsLegacyOrRevokedIdentity() throws Exception {
        var tenant = fixtures.activeTenant();
        UUID principal = UUID.randomUUID(), session = UUID.randomUUID(), connection = UUID.randomUUID();
        scope.run(tenant, () -> {
            jdbc.sql("INSERT INTO identity_binding(principal_id,tenant_id,provider,app_id,external_id) VALUES (:id,:t,'wecom','corp',:ext)").param("id", principal).param("t", tenant.value()).param("ext", principal.toString()).update();
            jdbc.sql("INSERT INTO access_member(tenant_id,actor_id,status,uses_seat,invited_by) VALUES (:t,:a,'active',true,'test')").param("t", tenant.value()).param("a", principal.toString()).update();
            jdbc.sql("INSERT INTO org_connection(id,tenant_id,provider,corp_id,app_id,secret_ref,unknown_user_policy,enabled,created_by) VALUES (:id,:t,'wecom','corp','app','TEST','jit_staff',true,'test')").param("id", connection).param("t", tenant.value()).update();
            jdbc.sql("INSERT INTO org_login_session(id,tenant_id,principal_id,connection_id,expires_at) VALUES (:id,:t,:p,:c,now()+interval '5 minutes')").param("id", session).param("t", tenant.value()).param("p", principal).param("c", connection).update();
        });
        try (var owner = DriverManager.getConnection(url, user, password)) {
            try {
                try (var bootstrap = owner.prepareStatement("SELECT platform_admin_bootstrap(?,?,?,?)")) {
                    bootstrap.setObject(1, tenant.value()); bootstrap.setObject(2, principal);
                    bootstrap.setString(3, "test"); bootstrap.setString(4, "isolated test"); bootstrap.execute();
                }
                String code = "plan-" + UUID.randomUUID().toString().substring(0, 8);
                var definition = new PlanDefinition(code, Set.of("survey.read"), Map.of("member.seats", 5L), 30);
                plans.publish(definition); plans.publish(definition);
                String personnel = "Bearer " + tokens.issueWithClaims(principal.toString(), tenant.value(), Map.of("iss", "mjy-platform", "sid", session.toString()));
                String cursor = code.substring(0, code.length() - 1);
                mvc.perform(get("/v1/platform/plan-catalog").param("after", cursor).param("limit", "1").header("Authorization", personnel))
                        .andExpect(status().isOk()).andExpect(jsonPath("$.items[0].planCode").value(code))
                        .andExpect(jsonPath("$.items[0].version").value(2)).andExpect(jsonPath("$.items[0].quotas['member.seats']").value(5));
                mvc.perform(get("/v1/platform/plan-catalog").header("Authorization", fixtures.operatorBearer())).andExpect(status().isForbidden());
                scope.run(tenant, () -> jdbc.sql("UPDATE access_member SET status='removed' WHERE actor_id=:a").param("a", principal.toString()).update());
                mvc.perform(get("/v1/platform/plan-catalog").header("Authorization", personnel)).andExpect(status().isForbidden());
            } finally {
                try (var d = owner.prepareStatement("DELETE FROM platform_admin_audit WHERE target_tenant=?")) { d.setObject(1, tenant.value()); d.executeUpdate(); }
                try (var d = owner.prepareStatement("DELETE FROM platform_admin_grant WHERE identity_tenant=?")) { d.setObject(1, tenant.value()); d.executeUpdate(); }
            }
        }
    }
}
