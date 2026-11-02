package cn.mjy.platform.platformadmin;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

import cn.mjy.platform.shared.tenant.TenantScope;
import cn.mjy.platform.support.TestTokens;
import cn.mjy.platform.tenant.TenantFixtures;
import java.sql.DriverManager;
import java.util.Map;
import java.util.UUID;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.Executors;
import java.util.concurrent.TimeUnit;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.webmvc.test.autoconfigure.AutoConfigureMockMvc;
import org.springframework.http.MediaType;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.test.web.servlet.MockMvc;

@SpringBootTest(properties = {"platform.admin.enforce-personnel=true", "PLATFORM_PROBE_HMAC_SECRET=isolated-probe-key-for-tests-only-000000000000", "PLATFORM_PROBE_INSTALLATION_ID=10000000-0000-4000-8000-000000000001", "PLATFORM_ENGINE_EVENTS_SECRET=isolated-events-key-for-tests-only-00000000000"})
@AutoConfigureMockMvc
class PlatformEngineManagementHttpTest {
    @Autowired MockMvc mvc; @Autowired TenantFixtures fixtures; @Autowired TenantScope scope;
    @Autowired JdbcClient jdbc; @Autowired TestTokens tokens;
    @Value("${spring.flyway.url}") String url; @Value("${spring.flyway.user}") String user; @Value("${spring.flyway.password}") String password;

    @Test void personnelManagesOnlyTargetTenantAndRevocationTakesEffect() throws Exception {
        var identity = fixtures.activeTenant(); var target = fixtures.activeTenant(); var other = fixtures.activeTenant();
        UUID principal = UUID.randomUUID(), session = UUID.randomUUID(), connection = UUID.randomUUID();
        scope.run(identity, () -> {
            jdbc.sql("INSERT INTO identity_binding(principal_id,tenant_id,provider,app_id,external_id) VALUES (:id,:t,'wecom','corp',:ext)").param("id", principal).param("t", identity.value()).param("ext", principal.toString()).update();
            jdbc.sql("INSERT INTO access_member(tenant_id,actor_id,status,uses_seat,invited_by) VALUES (:t,:a,'active',true,'test')").param("t", identity.value()).param("a", principal.toString()).update();
            jdbc.sql("INSERT INTO org_connection(id,tenant_id,provider,corp_id,app_id,secret_ref,unknown_user_policy,enabled,created_by) VALUES (:id,:t,'wecom','corp','app','TEST','jit_staff',true,'test')").param("id", connection).param("t", identity.value()).update();
            jdbc.sql("INSERT INTO org_login_session(id,tenant_id,principal_id,connection_id,expires_at) VALUES (:id,:t,:p,:c,now()+interval '5 minutes')").param("id", session).param("t", identity.value()).param("p", principal).param("c", connection).update();
        });
        try (var owner = DriverManager.getConnection(url, user, password)) {
            try {
                try (var bootstrap = owner.prepareStatement("SELECT platform_admin_bootstrap(?,?,?,?)")) {
                    bootstrap.setObject(1, identity.value()); bootstrap.setObject(2, principal);
                    bootstrap.setString(3, "test"); bootstrap.setString(4, "isolated test"); bootstrap.execute();
                }
                String personnel = "Bearer " + tokens.issueWithClaims(principal.toString(), identity.value(), Map.of("iss", "mjy-platform", "sid", session.toString()));
                String id = "engine-" + UUID.randomUUID().toString().substring(0, 8);
                String path = "/v1/platform/tenants/" + target + "/engine-instances";
                String register = "{\"id\":\"" + id + "\",\"baseUrl\":\"https://engine.invalid\"}";
                mvc.perform(post(path).header("Authorization", personnel).contentType(MediaType.APPLICATION_JSON).content(register))
                        .andExpect(status().isCreated()).andExpect(jsonPath("$.status").value("active"));
                mvc.perform(get(path).header("Authorization", personnel)).andExpect(status().isOk()).andExpect(jsonPath("$[0].id").value(id));
                mvc.perform(post(path + "/" + id + "/status").header("Authorization", personnel).contentType(MediaType.APPLICATION_JSON).content("{\"status\":\"draining\"}"))
                        .andExpect(status().isOk());
                mvc.perform(post(path + "/" + id + "/status").header("Authorization", personnel).contentType(MediaType.APPLICATION_JSON).content("{\"status\":\"retired\"}"))
                        .andExpect(status().isOk());
                mvc.perform(post(path + "/" + id + "/status").header("Authorization", personnel).contentType(MediaType.APPLICATION_JSON).content("{\"status\":\"active\"}"))
                        .andExpect(status().isConflict());
                mvc.perform(post("/v1/platform/tenants/" + other + "/engine-instances/" + id + "/status").header("Authorization", personnel)
                        .contentType(MediaType.APPLICATION_JSON).content("{\"status\":\"active\"}")).andExpect(status().isNotFound());
                assertThat(scope.call(target, () -> jdbc.sql("SELECT action FROM audit_log WHERE actor_id=:actor AND resource=:resource ORDER BY id")
                        .param("actor", principal.toString()).param("resource", "engine_instance/" + id).query(String.class).list()))
                        .containsExactly("engine_instance.register", "engine_instance.status:active->draining", "engine_instance.status:draining->retired");
                String failedId = id + "-audit";
                try (var statement = owner.createStatement()) {
                    statement.execute("CREATE FUNCTION test_engine_audit_failure() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'injected'; END $$");
                    statement.execute("CREATE TRIGGER test_engine_audit_failure BEFORE INSERT ON audit_log FOR EACH ROW WHEN (NEW.resource = 'engine_instance/" + failedId + "') EXECUTE FUNCTION test_engine_audit_failure()");
                }
                try {
                    assertThatThrownBy(() -> mvc.perform(post(path).header("Authorization", personnel).contentType(MediaType.APPLICATION_JSON)
                            .content(register.replace(id, failedId)))).isInstanceOf(Exception.class).hasStackTraceContaining("injected");
                    assertThat(scope.call(target, () -> jdbc.sql("SELECT count(*) FROM engine_instance WHERE id=:id")
                            .param("id", failedId).query(Long.class).single())).isZero();
                } finally {
                    try (var statement = owner.createStatement()) {
                        statement.execute("DROP TRIGGER test_engine_audit_failure ON audit_log");
                        statement.execute("DROP FUNCTION test_engine_audit_failure()");
                    }
                }
                mvc.perform(get(path).header("Authorization", fixtures.operatorBearer())).andExpect(status().isForbidden());
                var closingTarget = fixtures.activeTenant();
                String closingPath = "/v1/platform/tenants/" + closingTarget + "/engine-instances";
                try (var closing = DriverManager.getConnection(url, user, password); var executor = Executors.newSingleThreadExecutor()) {
                    closing.setAutoCommit(false);
                    try (var update = closing.prepareStatement("UPDATE tenant SET status='closed' WHERE id=?")) { update.setObject(1, closingTarget.value()); update.executeUpdate(); }
                    var started = new CountDownLatch(1);
                    var result = executor.submit(() -> { started.countDown(); return mvc.perform(post(closingPath).header("Authorization", personnel)
                            .contentType(MediaType.APPLICATION_JSON).content(register.replace(id, id + "-race"))).andReturn().getResponse().getStatus(); });
                    assertThat(started.await(5, TimeUnit.SECONDS)).isTrue();
                    boolean blocked = false;
                    for (int attempt = 0; attempt < 100 && !blocked; attempt++) {
                        try (var statement = owner.createStatement(); var locks = statement.executeQuery("SELECT EXISTS(SELECT 1 FROM pg_locks WHERE NOT granted AND locktype IN ('advisory','transactionid'))")) { locks.next(); blocked = locks.getBoolean(1); }
                        if (!blocked) Thread.sleep(20);
                    }
                    closing.commit();
                    assertThat(blocked).isTrue();
                    assertThat(result.get(5, TimeUnit.SECONDS)).isEqualTo(409);
                }
                assertThat(scope.call(closingTarget, () -> jdbc.sql("SELECT count(*) FROM engine_instance").query(Long.class).single())).isZero();
                scope.run(identity, () -> jdbc.sql("UPDATE access_member SET status='removed' WHERE actor_id=:a").param("a", principal.toString()).update());
                mvc.perform(get(path).header("Authorization", personnel)).andExpect(status().isForbidden());
                mvc.perform(post(path).header("Authorization", personnel).contentType(MediaType.APPLICATION_JSON).content(register)).andExpect(status().isForbidden());
            } finally {
                try (var d = owner.prepareStatement("DELETE FROM platform_admin_audit WHERE target_tenant=?")) { d.setObject(1, identity.value()); d.executeUpdate(); }
                try (var d = owner.prepareStatement("DELETE FROM platform_admin_grant WHERE identity_tenant=?")) { d.setObject(1, identity.value()); d.executeUpdate(); }
            }
        }
    }
}
