package cn.mjy.platform.platformadmin;

import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.assertj.core.api.Assertions.assertThat;
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
import java.util.concurrent.Executors;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.webmvc.test.autoconfigure.AutoConfigureMockMvc;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.http.MediaType;
import org.springframework.test.web.servlet.MockMvc;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

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
                mvc.perform(get("/v1/platform/plan-catalog/definitions").header("Authorization", personnel))
                        .andExpect(status().isOk()).andExpect(jsonPath("$.capabilities[?(@.code == 'survey.read')]").exists())
                        .andExpect(jsonPath("$.meters[?(@.code == 'member.seats')]").exists());
                String cursor = code.substring(0, code.length() - 1);
                mvc.perform(get("/v1/platform/plan-catalog").param("after", cursor).param("limit", "1").header("Authorization", personnel))
                        .andExpect(status().isOk()).andExpect(jsonPath("$.items[0].planCode").value(code))
                        .andExpect(jsonPath("$.items[0].version").value(2)).andExpect(jsonPath("$.items[0].quotas['member.seats']").value("5"));
                String concurrentCode = "race-" + UUID.randomUUID().toString().substring(0, 8);
                String publish = """
                        {"planCode":"%s","capabilities":["survey.read"],"quotas":{"member.seats":"5","response.valid_completed":"9223372036854775807"},"exportWindowDays":30,"expectedVersion":0}
                        """.formatted(concurrentCode);
                mvc.perform(post("/v1/platform/plans").header("Authorization", personnel).contentType(MediaType.APPLICATION_JSON)
                                .content(publish.replace(",\"expectedVersion\":0", "")))
                        .andExpect(status().isBadRequest());
                assertThat(plans.versionsOf(concurrentCode)).isEmpty();
                try (var executor = Executors.newFixedThreadPool(2)) {
                    var start = new CountDownLatch(1);
                    var first = executor.submit(() -> { start.await(); return mvc.perform(post("/v1/platform/plans").header("Authorization", personnel)
                            .contentType(MediaType.APPLICATION_JSON).content(publish)).andReturn().getResponse().getStatus(); });
                    var second = executor.submit(() -> { start.await(); return mvc.perform(post("/v1/platform/plans").header("Authorization", personnel)
                            .contentType(MediaType.APPLICATION_JSON).content(publish)).andReturn().getResponse().getStatus(); });
                    start.countDown();
                    assertThat(java.util.List.of(first.get(), second.get())).containsExactlyInAnyOrder(201, 409);
                }
                try (var audit = owner.prepareStatement("SELECT count(*) FROM platform_plan_audit WHERE actor_subject=? AND plan_code=?")) {
                    audit.setString(1, principal.toString()); audit.setString(2, concurrentCode);
                    try (var result = audit.executeQuery()) { result.next(); assertThat(result.getLong(1)).isEqualTo(1); }
                }
                assertThatThrownBy(() -> jdbc.sql("UPDATE platform_plan_audit SET actor_subject='forged'").update())
                        .isInstanceOf(org.springframework.dao.DataAccessException.class);
                mvc.perform(get("/v1/platform/plan-catalog/" + concurrentCode + "/versions").header("Authorization", personnel))
                        .andExpect(status().isOk()).andExpect(jsonPath("$.items[0].version").value(1))
                        .andExpect(jsonPath("$.items[0].quotas['response.valid_completed']").value("9223372036854775807"));
                mvc.perform(post("/v1/platform/plans").header("Authorization", personnel).contentType(MediaType.APPLICATION_JSON)
                                .content(publish.replace("9223372036854775807", "9007199254740993").replace("\"expectedVersion\":0", "\"expectedVersion\":1")))
                        .andExpect(status().isCreated()).andExpect(jsonPath("$.version").value(2));
                mvc.perform(get("/v1/platform/plan-catalog/" + concurrentCode + "/versions").param("after", "1").header("Authorization", personnel))
                        .andExpect(status().isOk()).andExpect(jsonPath("$.items[0].version").value(2))
                        .andExpect(jsonPath("$.items[0].quotas['response.valid_completed']").value("9007199254740993"));
                var target = fixtures.provisionedTenant();
                String onboard = """
                        {"planVersionId":"%s","kind":"TRIAL","days":30,"ownerActorId":"owner-1"}
                        """.formatted(plans.versionsOf(concurrentCode).getFirst().id());
                String path = "/v1/platform/tenants/" + target.id() + "/onboarding";
                mvc.perform(post(path).header("Authorization", personnel).contentType(MediaType.APPLICATION_JSON).content(onboard))
                        .andExpect(status().isOk()).andExpect(jsonPath("$.ownerActorId").value("owner-1"));
                mvc.perform(post(path).header("Authorization", personnel).contentType(MediaType.APPLICATION_JSON).content(onboard))
                        .andExpect(status().isConflict());
                assertThat(scope.call(target.id(), () -> jdbc.sql("SELECT actor_id FROM audit_log WHERE action='tenant.onboard'")
                        .query(String.class).list())).containsExactly(principal.toString());
                var closingTarget = fixtures.provisionedTenant();
                String closingPath = "/v1/platform/tenants/" + closingTarget.id() + "/onboarding";
                try (var closing = DriverManager.getConnection(url, user, password);
                     var executor = Executors.newSingleThreadExecutor()) {
                    closing.setAutoCommit(false);
                    try (var update = closing.prepareStatement("UPDATE tenant SET status='closed' WHERE id=?")) {
                        update.setObject(1, closingTarget.id().value()); update.executeUpdate();
                    }
                    var started = new CountDownLatch(1);
                    var result = executor.submit(() -> { started.countDown(); return mvc.perform(post(closingPath).header("Authorization", personnel)
                            .contentType(MediaType.APPLICATION_JSON).content(onboard)).andReturn().getResponse().getStatus(); });
                    assertThat(started.await(5, TimeUnit.SECONDS)).isTrue();
                    boolean blocked = false;
                    for (int attempt = 0; attempt < 100 && !blocked; attempt++) {
                        try (var statement = owner.createStatement(); var locks = statement.executeQuery("SELECT EXISTS(SELECT 1 FROM pg_locks WHERE locktype='advisory' AND NOT granted)")) {
                            locks.next(); blocked = locks.getBoolean(1);
                        }
                        if (!blocked) Thread.sleep(20);
                    }
                    closing.commit();
                    assertThat(blocked).isTrue();
                    assertThat(result.get(5, TimeUnit.SECONDS)).isEqualTo(409);
                }
                assertThat(scope.call(closingTarget.id(), () -> jdbc.sql("SELECT count(*) FROM subscription").query(Long.class).single())).isZero();
                mvc.perform(post("/v1/platform/plans").header("Authorization", personnel).contentType(MediaType.APPLICATION_JSON)
                                .content(publish.replace("9223372036854775807", "9223372036854775808")))
                        .andExpect(status().isBadRequest());
                String failureCode = "fail-" + UUID.randomUUID().toString().substring(0, 8);
                try (var statement = owner.createStatement()) {
                    statement.execute("CREATE FUNCTION test_plan_audit_failure() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'injected'; END $$");
                    statement.execute("CREATE TRIGGER test_plan_audit_failure BEFORE INSERT ON platform_plan_audit FOR EACH ROW WHEN (NEW.plan_code = '" + failureCode + "') EXECUTE FUNCTION test_plan_audit_failure()");
                }
                try {
                    assertThatThrownBy(() -> mvc.perform(post("/v1/platform/plans").header("Authorization", personnel)
                            .contentType(MediaType.APPLICATION_JSON).content(publish.replace(concurrentCode, failureCode))))
                            .isInstanceOf(Exception.class).hasStackTraceContaining("injected");
                    assertThat(plans.versionsOf(failureCode)).isEmpty();
                } finally {
                    try (var statement = owner.createStatement()) {
                        statement.execute("DROP TRIGGER test_plan_audit_failure ON platform_plan_audit");
                        statement.execute("DROP FUNCTION test_plan_audit_failure()");
                    }
                }
                mvc.perform(get("/v1/platform/plan-catalog").header("Authorization", fixtures.operatorBearer())).andExpect(status().isForbidden());
                scope.run(tenant, () -> jdbc.sql("UPDATE access_member SET status='removed' WHERE actor_id=:a").param("a", principal.toString()).update());
                mvc.perform(get("/v1/platform/plan-catalog").header("Authorization", personnel)).andExpect(status().isForbidden());
                mvc.perform(post(path).header("Authorization", personnel).contentType(MediaType.APPLICATION_JSON).content(onboard)).andExpect(status().isForbidden());
            } finally {
                try (var d = owner.prepareStatement("DELETE FROM platform_admin_audit WHERE target_tenant=?")) { d.setObject(1, tenant.value()); d.executeUpdate(); }
                try (var d = owner.prepareStatement("DELETE FROM platform_admin_grant WHERE identity_tenant=?")) { d.setObject(1, tenant.value()); d.executeUpdate(); }
            }
        }
    }
}
