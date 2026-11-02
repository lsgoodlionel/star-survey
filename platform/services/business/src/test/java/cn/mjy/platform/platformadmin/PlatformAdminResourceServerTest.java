package cn.mjy.platform.platformadmin;

import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

import cn.mjy.platform.shared.TenantId;
import cn.mjy.platform.shared.tenant.TenantScope;
import cn.mjy.platform.support.TestTokens;
import cn.mjy.platform.tenant.TenantFixtures;
import java.sql.DriverManager;
import java.time.Instant;
import java.util.Map;
import java.util.UUID;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.webmvc.test.autoconfigure.AutoConfigureMockMvc;
import org.springframework.context.annotation.Import;
import org.springframework.http.ResponseEntity;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.security.core.Authentication;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RestController;

/** Test-only endpoint proves the policy is reached through the real JWT resource-server filter and sid validator. */
@SpringBootTest
@AutoConfigureMockMvc
@Import(PlatformAdminResourceServerTest.CheckController.class)
class PlatformAdminResourceServerTest {
    @Autowired MockMvc mvc;
    @Autowired TenantFixtures fixtures;
    @Autowired TenantScope scope;
    @Autowired JdbcClient jdbc;
    @Autowired TestTokens tokens;
    @Value("${spring.flyway.url}") String url;
    @Value("${spring.flyway.user}") String ownerUser;
    @Value("${spring.flyway.password}") String ownerPassword;

    @RestController
    static class CheckController {
        private final JdbcClient jdbc;
        CheckController(JdbcClient jdbc) { this.jdbc = jdbc; }
        @GetMapping("/v1/test-platform-admin-check")
        ResponseEntity<Void> check(Authentication auth) {
            return new PlatformAdminAuthorization(new JdbcPlatformAdminGrantLookup(jdbc)).isAdmin(auth)
                    ? ResponseEntity.ok().build() : ResponseEntity.status(403).build();
        }
        @PostMapping("/v1/test-platform-admin-actor")
        Map<String, String> actor(@RequestBody Map<String, String> ignored) {
            var actor = new PlatformAdminActorResolver(new PlatformAdminAuthorization(new JdbcPlatformAdminGrantLookup(jdbc))).require();
            return Map.of("tenant", actor.tenant().toString(), "subject", actor.subject().toString());
        }
        @GetMapping("/v1/test-platform-capabilities")
        Map<String, Boolean> capabilities() {
            return new PlatformCapabilityResolver(new PlatformAdminAuthorization(new JdbcPlatformAdminGrantLookup(jdbc))).current();
        }
    }

    @Test void verifiedSessionIsRequiredEvenForASyntheticActiveGrant() throws Exception {
        org.assertj.core.api.Assertions.assertThat(jdbc.sql("SELECT current_user").query(String.class).single()).isEqualTo("platform_app");
        org.assertj.core.api.Assertions.assertThat(jdbc.sql("SELECT has_table_privilege(current_user, 'platform_admin_grant', 'INSERT,UPDATE,DELETE')")
                .query(Boolean.class).single()).isFalse();
        TenantId tenant = fixtures.activeTenant();
        UUID principal = UUID.randomUUID(), target = UUID.randomUUID(), connection = UUID.randomUUID(), session = UUID.randomUUID(), expiredSession = UUID.randomUUID();
        scope.run(tenant, () -> {
            jdbc.sql("INSERT INTO identity_binding(principal_id, tenant_id, provider, app_id, external_id) VALUES (:id,:tenant,'wecom','corp',:ext)")
                    .param("id", principal).param("tenant", tenant.value()).param("ext", principal.toString()).update();
            jdbc.sql("INSERT INTO access_member(tenant_id,actor_id,status,uses_seat,invited_by) VALUES (:tenant,:actor,'active',true,'test')")
                    .param("tenant", tenant.value()).param("actor", principal.toString()).update();
            jdbc.sql("INSERT INTO org_connection(id,tenant_id,provider,corp_id,app_id,secret_ref,unknown_user_policy,enabled,created_by) VALUES (:id,:tenant,'wecom','corp','app','TEST','jit_staff',true,'test')")
                    .param("id", connection).param("tenant", tenant.value()).update();
            jdbc.sql("INSERT INTO org_login_session(id,tenant_id,principal_id,connection_id,expires_at) VALUES (:id,:tenant,:principal,:connection,now()+interval '5 minutes')")
                    .param("id", session).param("tenant", tenant.value()).param("principal", principal).param("connection", connection).update();
            jdbc.sql("INSERT INTO org_login_session(id,tenant_id,principal_id,connection_id,expires_at) VALUES (:id,:tenant,:principal,:connection,now()-interval '5 minutes')")
                    .param("id", expiredSession).param("tenant", tenant.value()).param("principal", principal).param("connection", connection).update();
            jdbc.sql("INSERT INTO identity_binding(principal_id,tenant_id,provider,app_id,external_id) VALUES (:id,:tenant,'wecom','corp',:ext)")
                    .param("id", target).param("tenant", tenant.value()).param("ext", target.toString()).update();
            jdbc.sql("INSERT INTO access_member(tenant_id,actor_id,status,uses_seat,invited_by) VALUES (:tenant,:actor,'active',true,'test')")
                    .param("tenant", tenant.value()).param("actor", target.toString()).update();
        });
        try (var owner = DriverManager.getConnection(url, ownerUser, ownerPassword)) {
          try {
            try (var bootstrap = owner.prepareStatement("SELECT platform_admin_bootstrap(?,?,?,?)")) {
                bootstrap.setObject(1, tenant.value()); bootstrap.setObject(2, principal);
                bootstrap.setString(3, "test-trace"); bootstrap.setString(4, "isolated test"); bootstrap.execute();
            }
        String valid = token(principal, tenant, Map.of("iss", "mjy-platform", "sid", session.toString()));
        getCheck(valid, 200);
        mvc.perform(get("/v1/test-platform-capabilities").header("Authorization", "Bearer " + valid))
                .andExpect(status().isOk()).andExpect(jsonPath("$.managePlatform").value(true));
        String machine = token(principal, tenant, Map.of("iss", "mjy-platform", "roles", java.util.List.of("platform_operator")));
        mvc.perform(get("/v1/test-platform-capabilities").header("Authorization", "Bearer " + machine))
                .andExpect(status().isOk()).andExpect(jsonPath("$.managePlatform").value(false));
        mvc.perform(post("/v1/test-platform-admin-actor").header("Authorization", "Bearer " + valid)
                        .contentType("application/json").content("{\"actor\":\"forged-actor\"}"))
                .andExpect(status().isOk()).andExpect(jsonPath("$.tenant").value(tenant.toString()))
                .andExpect(jsonPath("$.subject").value(principal.toString()));
        String mutation = "{\"targetTenant\":\"" + tenant + "\",\"targetSubject\":\"" + target
                + "\",\"grant\":true,\"reason\":\"isolated test\",\"actor\":\"forged\","
                + "\"actorTenant\":\"" + UUID.randomUUID() + "\",\"sid\":\"" + UUID.randomUUID() + "\"}";
        mvc.perform(post("/v1/platform-admin/grants/change").header("Authorization", "Bearer " + valid)
                        .contentType("application/json").content(mutation))
                .andExpect(status().isOk()).andExpect(jsonPath("$.changed").value(true));
        mvc.perform(post("/v1/platform-admin/grants/change").header("Authorization", "Bearer " + valid)
                        .contentType("application/json").content(mutation))
                .andExpect(status().isOk()).andExpect(jsonPath("$.changed").value(false));
        try (var audit = owner.prepareStatement("SELECT actor_subject FROM platform_admin_audit WHERE target_subject=? AND action='grant'")) {
            audit.setObject(1, target);
            try (var result = audit.executeQuery()) {
                org.assertj.core.api.Assertions.assertThat(result.next()).isTrue();
                org.assertj.core.api.Assertions.assertThat(result.getObject(1, UUID.class)).isEqualTo(principal);
                org.assertj.core.api.Assertions.assertThat(result.next()).isFalse();
            }
        }
        mvc.perform(post("/v1/platform-admin/grants/change").header("Authorization", "Bearer " + valid)
                        .contentType("application/json").content(mutation.replace("\"grant\":true", "\"grant\":false")))
                .andExpect(status().isOk()).andExpect(jsonPath("$.changed").value(true));
        mvc.perform(post("/v1/platform-admin/grants/change").header("Authorization", "Bearer " + machine)
                        .contentType("application/json").content(mutation)).andExpect(status().isForbidden());
        mvc.perform(post("/v1/platform-admin/grants/change").contentType("application/json").content(mutation))
                .andExpect(status().isUnauthorized());
        for (String bad : java.util.List.of(
                token(principal, tenant, Map.of("iss", "mjy-platform", "sid", UUID.randomUUID().toString())),
                token(principal, tenant, Map.of("iss", "mjy-platform", "sid", expiredSession.toString())),
                token(UUID.randomUUID(), tenant, Map.of("iss", "mjy-platform", "sid", session.toString())),
                token(principal, fixtures.activeTenant(), Map.of("iss", "mjy-platform", "sid", session.toString())))) {
            mvc.perform(post("/v1/platform-admin/grants/change").header("Authorization", "Bearer " + bad)
                            .contentType("application/json").content(mutation)).andExpect(status().isUnauthorized());
        }
        String selfRevoke = mutation.replace(target.toString(), principal.toString()).replace("\"grant\":true", "\"grant\":false");
        mvc.perform(post("/v1/platform-admin/grants/change").header("Authorization", "Bearer " + valid)
                        .contentType("application/json").content(selfRevoke)).andExpect(status().isConflict());
        String unknownTarget = mutation.replace(target.toString(), UUID.randomUUID().toString());
        mvc.perform(post("/v1/platform-admin/grants/change").header("Authorization", "Bearer " + valid)
                        .contentType("application/json").content(unknownTarget)).andExpect(status().isConflict());
        getCheck(token(principal, tenant, Map.of("iss", "wrong-issuer", "sid", session.toString())), 403);
        getCheck(token(principal, tenant, Map.of("iss", "mjy-platform", "roles", java.util.List.of("platform_operator"))), 403);
        getCheck(token(principal, tenant, Map.of("iss", "mjy-platform", "sid", UUID.randomUUID().toString())), 401);
        getCheck(token(principal, tenant, Map.of("iss", "mjy-platform", "sid", expiredSession.toString())), 401);
        getCheck(token(UUID.randomUUID(), tenant, Map.of("iss", "mjy-platform", "sid", session.toString())), 401);
        getCheck(token(principal, fixtures.activeTenant(), Map.of("iss", "mjy-platform", "sid", session.toString())), 401);
        getCheck(token(principal, tenant, Map.of("iss", "mjy-platform", "sid", session.toString(), "iat", Instant.now().minusSeconds(1200), "exp", Instant.now().minusSeconds(600))), 401);
        scope.run(tenant, () -> jdbc.sql("UPDATE access_member SET status='removed' WHERE actor_id=:actor")
                .param("actor", principal.toString()).update());
        getCheck(valid, 403);
        mvc.perform(post("/v1/platform-admin/grants/change").header("Authorization", "Bearer " + valid)
                        .contentType("application/json").content(mutation)).andExpect(status().isForbidden());
        mvc.perform(get("/v1/test-platform-capabilities").header("Authorization", "Bearer " + valid))
                .andExpect(status().isOk()).andExpect(jsonPath("$.managePlatform").value(false));
        mvc.perform(post("/v1/test-platform-admin-actor").header("Authorization", "Bearer " + valid)
                        .contentType("application/json").content("{\"actor\":\"forged-actor\"}"))
                .andExpect(status().isForbidden());
        scope.run(tenant, () -> jdbc.sql("UPDATE access_member SET status='active' WHERE actor_id=:actor")
                .param("actor", principal.toString()).update());
        getCheck(valid, 403);
        recover(owner, tenant, principal);
        getCheck(valid, 200);
        scope.run(tenant, () -> jdbc.sql("UPDATE org_connection SET enabled=false WHERE id=:id").param("id", connection).update());
        getCheck(valid, 403);
        scope.run(tenant, () -> jdbc.sql("UPDATE org_connection SET enabled=true WHERE id=:id").param("id", connection).update());
        getCheck(valid, 403);
        recover(owner, tenant, principal);
        getCheck(valid, 200);
        scope.run(tenant, () -> jdbc.sql("UPDATE org_login_session SET revoked_at=now(), revoke_reason='logout' WHERE id=:id").param("id", session).update());
        getCheck(valid, 401);
        mvc.perform(post("/v1/platform-admin/grants/change").header("Authorization", "Bearer " + valid)
                        .contentType("application/json").content(mutation)).andExpect(status().isUnauthorized());
          } finally {
            try (var cleanup = owner.prepareStatement("DELETE FROM platform_admin_audit WHERE target_tenant = ?")) {
                cleanup.setObject(1, tenant.value()); cleanup.executeUpdate();
            }
            try (var cleanup = owner.prepareStatement("DELETE FROM platform_admin_grant WHERE identity_tenant = ?")) {
                cleanup.setObject(1, tenant.value()); cleanup.executeUpdate();
            }
          }
        }
    }
    private void recover(java.sql.Connection owner, TenantId tenant, UUID principal) throws Exception {
        try (var statement = owner.prepareStatement("SELECT platform_admin_recover(?,?,?,?)")) {
            statement.setObject(1, tenant.value()); statement.setObject(2, principal);
            statement.setString(3, "test-recovery"); statement.setString(4, "isolated test");
            statement.execute();
        }
    }
    private String token(UUID principal, TenantId tenant, Map<String, Object> claims) {
        return tokens.issueWithClaims(principal.toString(), tenant.value(), claims);
    }
    private void getCheck(String token, int expected) throws Exception {
        mvc.perform(get("/v1/test-platform-admin-check").header("Authorization", "Bearer " + token))
                .andExpect(status().is(expected));
    }
}
