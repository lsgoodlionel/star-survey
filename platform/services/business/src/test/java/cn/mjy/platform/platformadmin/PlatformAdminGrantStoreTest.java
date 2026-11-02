package cn.mjy.platform.platformadmin;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import cn.mjy.platform.tenant.TenantFixtures;
import java.sql.Connection;
import java.sql.DriverManager;
import java.util.UUID;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.dao.DataAccessException;
import org.springframework.jdbc.core.simple.JdbcClient;

@SpringBootTest
class PlatformAdminGrantStoreTest {
    @Autowired JdbcClient jdbc;
    @Autowired TenantFixtures fixtures;
    @Value("${spring.flyway.url}") String url;
    @Value("${spring.flyway.user}") String ownerUser;
    @Value("${spring.flyway.password}") String ownerPassword;

    @Test void bootstrapGrantRevokeAndAuditAreProtected() throws Exception {
        UUID tenant = fixtures.provisionedTenant().id().value(), first = UUID.randomUUID(), second = UUID.randomUUID();
        try (Connection owner = DriverManager.getConnection(url, ownerUser, ownerPassword)) {
            try {
                try (var scope = owner.prepareStatement("SELECT set_config('app.tenant_id', ?, false)")) {
                    scope.setString(1, tenant.toString()); scope.execute();
                }
                assertThatThrownBy(() -> {
                    try (var invalid = owner.prepareStatement("SELECT platform_admin_bootstrap(?,?,?)")) {
                        invalid.setObject(1, tenant); invalid.setObject(2, first); invalid.setString(3, "unknown"); invalid.execute();
                    }
                }).isInstanceOf(java.sql.SQLException.class);
                try (var binding = owner.prepareStatement("INSERT INTO identity_binding(principal_id,tenant_id,provider,app_id,external_id) VALUES (?,?,?,?,?)")) {
                    for (UUID subject : new UUID[] { first, second }) {
                        binding.setObject(1, subject); binding.setObject(2, tenant); binding.setString(3, "testorg");
                        binding.setString(4, "test"); binding.setString(5, subject.toString()); binding.executeUpdate();
                    }
                }
                try (var statement = owner.prepareStatement("SELECT platform_admin_bootstrap(?, ?, ?)") ) {
                    statement.setObject(1, tenant); statement.setObject(2, first); statement.setString(3, "test-bootstrap");
                    statement.execute();
                }
                assertThat(jdbc.sql("SELECT platform_admin_active(:tenant, :subject)").param("tenant", tenant)
                        .param("subject", first).query(Boolean.class).single()).isTrue();
                assertThatThrownBy(() -> {
                    try (var statement = owner.prepareStatement("SELECT platform_admin_bootstrap(?,?,?)")) {
                        statement.setObject(1, tenant); statement.setObject(2, second); statement.setString(3, "duplicate");
                        statement.execute();
                    }
                }).isInstanceOf(java.sql.SQLException.class);
                assertThat(jdbc.sql("SELECT platform_admin_active(:tenant,:subject)")
                        .param("tenant", UUID.randomUUID()).param("subject", first).query(Boolean.class).single()).isFalse();
                assertThatThrownBy(() -> jdbc.sql("SELECT * FROM platform_admin_grant").query().listOfRows())
                        .isInstanceOf(DataAccessException.class);
                assertThatThrownBy(() -> jdbc.sql("SELECT platform_admin_bootstrap(:tenant,:subject,'forbidden')")
                        .param("tenant", tenant).param("subject", second).query(Boolean.class).single())
                        .isInstanceOf(DataAccessException.class);
                assertThatThrownBy(() -> jdbc.sql("INSERT INTO platform_admin_grant(identity_tenant,subject) VALUES (:tenant,:subject)")
                        .param("tenant", tenant).param("subject", second).update()).isInstanceOf(DataAccessException.class);
                assertThatThrownBy(() -> change(tenant, first, tenant, second, true)).isInstanceOf(DataAccessException.class);
                assertThat(ownerChange(owner, tenant, first, tenant, second, true)).isTrue();
                assertThat(ownerChange(owner, tenant, first, tenant, first, false)).isTrue();
                assertThat(jdbc.sql("SELECT platform_admin_active(:tenant,:subject)").param("tenant", tenant)
                        .param("subject", first).query(Boolean.class).single()).isFalse();
                assertThatThrownBy(() -> ownerChange(owner, tenant, first, tenant, second, false)).isInstanceOf(java.sql.SQLException.class);
                assertThatThrownBy(() -> ownerChange(owner, tenant, second, tenant, second, false)).isInstanceOf(java.sql.SQLException.class);
                try (var query = owner.prepareStatement("SELECT count(*) FROM platform_admin_audit WHERE target_tenant = ?")) {
                    query.setObject(1, tenant);
                    try (var result = query.executeQuery()) { result.next(); assertThat(result.getLong(1)).isEqualTo(3); }
                }
            } finally {
                try (var cleanup = owner.prepareStatement("DELETE FROM platform_admin_audit WHERE target_tenant = ?")) {
                    cleanup.setObject(1, tenant); cleanup.executeUpdate();
                }
                try (var cleanup = owner.prepareStatement("DELETE FROM platform_admin_grant WHERE identity_tenant = ?")) {
                    cleanup.setObject(1, tenant); cleanup.executeUpdate();
                }
                try (var cleanup = owner.prepareStatement("DELETE FROM identity_binding WHERE tenant_id = ?")) {
                    cleanup.setObject(1, tenant); cleanup.executeUpdate();
                }
            }
        }
    }
    private boolean ownerChange(Connection owner, UUID actorTenant, UUID actor, UUID targetTenant, UUID target, boolean grant) throws Exception {
        try (var statement = owner.prepareStatement("SELECT platform_admin_change(?,?,?,?,?,?)")) {
            statement.setObject(1, actorTenant); statement.setObject(2, actor); statement.setObject(3, targetTenant);
            statement.setObject(4, target); statement.setBoolean(5, grant); statement.setString(6, "test-change");
            try (var result = statement.executeQuery()) { result.next(); return result.getBoolean(1); }
        }
    }
    private boolean change(UUID actorTenant, UUID actor, UUID targetTenant, UUID target, boolean grant) {
        return jdbc.sql("SELECT platform_admin_change(:at,:a,:tt,:t,:grant,'test-change')")
                .param("at", actorTenant).param("a", actor).param("tt", targetTenant).param("t", target)
                .param("grant", grant).query(Boolean.class).single();
    }
}
