package cn.mjy.platform.platformadmin;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import cn.mjy.platform.tenant.TenantFixtures;
import cn.mjy.platform.tenant.TenantService;
import cn.mjy.platform.tenant.TenantStatus;
import cn.mjy.platform.shared.TenantId;
import java.sql.Connection;
import java.sql.DriverManager;
import java.util.UUID;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.Executors;
import java.util.concurrent.TimeUnit;
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
    @Autowired TenantService tenants;
    @Value("${spring.flyway.url}") String url;
    @Value("${spring.flyway.user}") String ownerUser;
    @Value("${spring.flyway.password}") String ownerPassword;

    @Test void bootstrapGrantRevokeAndAuditAreProtected() throws Exception {
        UUID tenant = fixtures.activeTenant().value(), first = UUID.randomUUID(), second = UUID.randomUUID();
        UUID session = UUID.randomUUID(), expired = UUID.randomUUID(), revoked = UUID.randomUUID(), shortSession = UUID.randomUUID();
        try (Connection owner = DriverManager.getConnection(url, ownerUser, ownerPassword)) {
            try {
                try (var scope = owner.prepareStatement("SELECT set_config('app.tenant_id', ?, false)")) {
                    scope.setString(1, tenant.toString()); scope.execute();
                }
                assertThatThrownBy(() -> {
                    try (var invalid = owner.prepareStatement("SELECT platform_admin_bootstrap(?,?,?,?)")) {
                        invalid.setObject(1, tenant); invalid.setObject(2, first); invalid.setString(3, "unknown"); invalid.setString(4, "initial setup"); invalid.execute();
                    }
                }).isInstanceOf(java.sql.SQLException.class);
                try (var binding = owner.prepareStatement("INSERT INTO identity_binding(principal_id,tenant_id,provider,app_id,external_id) VALUES (?,?,?,?,?)")) {
                    for (UUID subject : new UUID[] { first, second }) {
                        binding.setObject(1, subject); binding.setObject(2, tenant); binding.setString(3, "wecom");
                        binding.setString(4, subject.toString()); binding.setString(5, subject.toString()); binding.executeUpdate();
                    }
                }
                try (var member = owner.prepareStatement("INSERT INTO access_member(tenant_id,actor_id,status,uses_seat,invited_by) VALUES (?,?,'active',true,'test')");
                     var connection = owner.prepareStatement("INSERT INTO org_connection(id,tenant_id,provider,corp_id,app_id,secret_ref,unknown_user_policy,enabled,created_by) VALUES (?,?,'wecom',?,'app','TEST','jit_staff',true,'test')")) {
                    for (UUID subject : new UUID[] { first, second }) {
                        member.setObject(1, tenant); member.setString(2, subject.toString()); member.executeUpdate();
                        connection.setObject(1, UUID.randomUUID()); connection.setObject(2, tenant);
                        connection.setString(3, subject.toString()); connection.executeUpdate();
                    }
                }
                try (var insert = owner.prepareStatement("INSERT INTO org_login_session(id,tenant_id,principal_id,connection_id,expires_at,revoked_at,revoke_reason) SELECT ?,?,?,id, CASE WHEN ? THEN now()-interval '1 minute' ELSE now()+interval '5 minutes' END, CASE WHEN ? THEN now() ELSE NULL END, CASE WHEN ? THEN 'logout' ELSE NULL END FROM org_connection WHERE tenant_id=? AND corp_id=?")) {
                    for (UUID id : new UUID[] { session, expired, revoked, shortSession }) {
                        insert.setObject(1, id); insert.setObject(2, tenant); insert.setObject(3, first);
                        insert.setBoolean(4, id.equals(expired)); insert.setBoolean(5, id.equals(revoked)); insert.setBoolean(6, id.equals(revoked));
                        insert.setObject(7, tenant); insert.setString(8, first.toString()); assertThat(insert.executeUpdate()).isEqualTo(1);
                    }
                }
                assertThatThrownBy(() -> {
                    try (var invalid = owner.prepareStatement("SELECT platform_admin_bootstrap(?,?,?,?)")) {
                        invalid.setObject(1, tenant); invalid.setObject(2, first); invalid.setString(3, "test"); invalid.setString(4, " "); invalid.execute();
                    }
                }).isInstanceOf(java.sql.SQLException.class);
                try (var statement = owner.prepareStatement("SELECT platform_admin_bootstrap(?, ?, ?, ?)") ) {
                    statement.setObject(1, tenant); statement.setObject(2, first); statement.setString(3, "test-bootstrap"); statement.setString(4, "initial setup");
                    statement.execute();
                }
                try (var check = owner.prepareStatement("SELECT platform_admin_identity_usable(?, ?), current_setting('app.tenant_id', true)")) {
                    check.setObject(1, UUID.randomUUID()); check.setObject(2, UUID.randomUUID());
                    try (var result = check.executeQuery()) {
                        result.next(); assertThat(result.getBoolean(1)).isFalse(); assertThat(result.getString(2)).isEqualTo(tenant.toString());
                    }
                }
                try (var source = owner.prepareStatement("SELECT identity_source FROM platform_admin_grant WHERE identity_tenant=? AND subject=?")) {
                    source.setObject(1, tenant); source.setObject(2, first);
                    try (var result = source.executeQuery()) { result.next(); assertThat(result.getString(1)).isEqualTo("org-session-v1"); }
                }
                assertThat(jdbc.sql("SELECT platform_admin_active(:tenant, :subject)").param("tenant", tenant)
                        .param("subject", first).query(Boolean.class).single()).isTrue();
                assertThatThrownBy(() -> {
                    try (var statement = owner.prepareStatement("SELECT platform_admin_bootstrap(?,?,?,?)")) {
                        statement.setObject(1, tenant); statement.setObject(2, second); statement.setString(3, "duplicate"); statement.setString(4, "initial setup");
                        statement.execute();
                    }
                }).isInstanceOf(java.sql.SQLException.class);
                assertThat(jdbc.sql("SELECT platform_admin_active(:tenant,:subject)")
                        .param("tenant", UUID.randomUUID()).param("subject", first).query(Boolean.class).single()).isFalse();
                assertThatThrownBy(() -> jdbc.sql("SELECT * FROM platform_admin_grant").query().listOfRows())
                        .isInstanceOf(DataAccessException.class);
                assertThatThrownBy(() -> jdbc.sql("SELECT platform_admin_bootstrap(:tenant,:subject,'forbidden','test')")
                        .param("tenant", tenant).param("subject", second).query(Boolean.class).single())
                        .isInstanceOf(DataAccessException.class);
                assertThatThrownBy(() -> jdbc.sql("INSERT INTO platform_admin_grant(identity_tenant,subject) VALUES (:tenant,:subject)")
                        .param("tenant", tenant).param("subject", second).update()).isInstanceOf(DataAccessException.class);
                assertThatThrownBy(() -> change(tenant, first, tenant, second, true)).isInstanceOf(DataAccessException.class);
                assertThatThrownBy(() -> jdbc.sql("SELECT platform_admin_change_session(:tenant,:actor,:sid,:tenant,:target,true,'trace','test')")
                        .param("tenant", tenant).param("actor", first).param("sid", session).param("target", second).query(Boolean.class).single())
                        .isInstanceOf(DataAccessException.class);
                for (UUID bad : new UUID[] { UUID.randomUUID(), expired, revoked }) {
                    assertThatThrownBy(() -> ownerSessionChange(owner, tenant, first, bad, second, true))
                            .isInstanceOf(java.sql.SQLException.class);
                }
                assertThatThrownBy(() -> ownerSessionChange(owner, tenant, second, session, first, true))
                        .isInstanceOf(java.sql.SQLException.class);
                try (var shortExpiry = owner.prepareStatement("UPDATE org_login_session SET expires_at=now()+interval '5 seconds' WHERE id=?")) {
                    shortExpiry.setObject(1, shortSession); shortExpiry.executeUpdate();
                }
                try (Connection blocker = DriverManager.getConnection(url, ownerUser, ownerPassword);
                     var pool = Executors.newSingleThreadExecutor()) {
                    blocker.setAutoCommit(false);
                    try (var scope = blocker.prepareStatement("SELECT set_config('app.tenant_id', ?, false)")) {
                        scope.setString(1, tenant.toString()); scope.execute();
                    }
                    try (var row = blocker.prepareStatement("SELECT id FROM org_login_session WHERE id=? FOR UPDATE")) {
                        row.setObject(1, shortSession); row.execute();
                    }
                    try {
                        var attempt = pool.submit(() -> {
                            try (Connection conn = DriverManager.getConnection(url, ownerUser, ownerPassword)) {
                                return ownerSessionChange(conn, tenant, first, shortSession, second, true);
                            }
                        });
                        assertThatThrownBy(() -> attempt.get(1, TimeUnit.SECONDS)).isInstanceOf(java.util.concurrent.TimeoutException.class);
                        Thread.sleep(4500);
                        blocker.rollback();
                        assertThatThrownBy(() -> attempt.get(10, TimeUnit.SECONDS))
                                .isInstanceOf(java.util.concurrent.ExecutionException.class)
                                .hasCauseInstanceOf(java.sql.SQLException.class);
                    } finally { blocker.rollback(); }
                }
                assertThat(ownerSessionChange(owner, tenant, first, session, second, true)).isTrue();
                assertThat(ownerSessionChange(owner, tenant, first, session, second, false)).isTrue();
                assertThat(ownerChange(owner, tenant, first, tenant, second, true)).isTrue();
                assertThatThrownBy(() -> {
                    try (var recover = owner.prepareStatement("SELECT platform_admin_recover(?,?,?,?)")) {
                        recover.setObject(1, tenant); recover.setObject(2, UUID.randomUUID());
                        recover.setString(3, "bad-recover"); recover.setString(4, "should not be permitted");
                        recover.execute();
                    }
                }).isInstanceOf(java.sql.SQLException.class).hasMessageContaining("zero usable administrators");
                var start = new CountDownLatch(1);
                try (var pool = Executors.newFixedThreadPool(2)) {
                    var firstAttempt = pool.submit(() -> concurrentSelfRevoke(tenant, first, start));
                    var secondAttempt = pool.submit(() -> concurrentSelfRevoke(tenant, second, start));
                    start.countDown();
                    assertThat(java.util.List.of(firstAttempt.get(15, TimeUnit.SECONDS), secondAttempt.get(15, TimeUnit.SECONDS)))
                            .containsExactlyInAnyOrder(true, false);
                }
                boolean firstStillActive = jdbc.sql("SELECT platform_admin_active(:tenant,:subject)")
                        .param("tenant", tenant).param("subject", first).query(Boolean.class).single();
                assertThat(ownerChange(owner, tenant, firstStillActive ? first : second, tenant,
                        firstStillActive ? second : first, true)).isTrue();
                owner.setAutoCommit(false);
                try {
                    try (var fault = owner.createStatement()) {
                        fault.execute("CREATE FUNCTION pg_temp.fail_admin_audit() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'injected audit failure'; END $$");
                        fault.execute("CREATE TRIGGER test_admin_audit_failure BEFORE INSERT ON platform_admin_audit FOR EACH ROW EXECUTE FUNCTION pg_temp.fail_admin_audit()");
                    }
                    assertThatThrownBy(() -> ownerSessionChange(owner, tenant, first, session, second, false))
                            .isInstanceOf(java.sql.SQLException.class);
                } finally {
                    owner.rollback(); owner.setAutoCommit(true);
                }
                assertThat(jdbc.sql("SELECT platform_admin_active(:tenant,:subject)").param("tenant", tenant)
                        .param("subject", second).query(Boolean.class).single()).isTrue();
                try (var disable = owner.prepareStatement("UPDATE org_connection SET enabled=false WHERE tenant_id=? AND corp_id=?")) {
                    disable.setObject(1, tenant); disable.setString(2, second.toString()); disable.executeUpdate();
                }
                assertThatThrownBy(() -> ownerChange(owner, tenant, first, tenant, first, false))
                        .isInstanceOf(java.sql.SQLException.class);
                try (var enable = owner.prepareStatement("UPDATE org_connection SET enabled=true WHERE tenant_id=? AND corp_id=?")) {
                    enable.setObject(1, tenant); enable.setString(2, second.toString()); enable.executeUpdate();
                }
                assertThat(jdbc.sql("SELECT platform_admin_active(:tenant,:subject)").param("tenant", tenant)
                        .param("subject", second).query(Boolean.class).single()).isFalse();
                assertThat(ownerChange(owner, tenant, first, tenant, second, true)).isTrue();
                try (var remove = owner.prepareStatement("UPDATE access_member SET status='removed' WHERE tenant_id=? AND actor_id=?")) {
                    remove.setObject(1, tenant); remove.setString(2, second.toString()); remove.executeUpdate();
                }
                assertThatThrownBy(() -> ownerChange(owner, tenant, first, tenant, first, false))
                        .isInstanceOf(java.sql.SQLException.class);
                try (var restore = owner.prepareStatement("UPDATE access_member SET status='active' WHERE tenant_id=? AND actor_id=?")) {
                    restore.setObject(1, tenant); restore.setString(2, second.toString()); restore.executeUpdate();
                }
                assertThat(jdbc.sql("SELECT platform_admin_active(:tenant,:subject)").param("tenant", tenant)
                        .param("subject", second).query(Boolean.class).single()).isFalse();
                assertThat(ownerChange(owner, tenant, first, tenant, second, true)).isTrue();
                try (var revoke = owner.prepareStatement("INSERT INTO identity_binding_revocation(tenant_id,principal_id,reason,revoked_by) VALUES (?,?,'admin','test')")) {
                    revoke.setObject(1, tenant); revoke.setObject(2, second); revoke.executeUpdate();
                }
                assertThat(jdbc.sql("SELECT platform_admin_active(:tenant,:subject)").param("tenant", tenant)
                        .param("subject", second).query(Boolean.class).single()).isFalse();
                assertThatThrownBy(() -> ownerSessionChange(owner, tenant, first, session, second, true))
                        .isInstanceOf(java.sql.SQLException.class);
                assertThatThrownBy(() -> ownerChange(owner, tenant, second, tenant, first, false))
                        .isInstanceOf(java.sql.SQLException.class);
                assertThatThrownBy(() -> ownerChange(owner, tenant, first, tenant, first, false))
                        .isInstanceOf(java.sql.SQLException.class);
                try (var restore = owner.prepareStatement("UPDATE identity_binding_revocation SET reinstated_at=now(),reinstated_by='test' WHERE tenant_id=? AND principal_id=?")) {
                    restore.setObject(1, tenant); restore.setObject(2, second); restore.executeUpdate();
                }
                assertThat(jdbc.sql("SELECT platform_admin_active(:tenant,:subject)").param("tenant", tenant)
                        .param("subject", second).query(Boolean.class).single()).isFalse();
                assertThat(ownerChange(owner, tenant, first, tenant, second, true)).isTrue();
                assertThat(ownerChange(owner, tenant, first, tenant, first, false)).isTrue();
                assertThat(jdbc.sql("SELECT platform_admin_active(:tenant,:subject)").param("tenant", tenant)
                        .param("subject", first).query(Boolean.class).single()).isFalse();
                assertThatThrownBy(() -> ownerChange(owner, tenant, first, tenant, second, false)).isInstanceOf(java.sql.SQLException.class);
                assertThatThrownBy(() -> ownerChange(owner, tenant, second, tenant, second, false)).isInstanceOf(java.sql.SQLException.class);
                tenants.changeStatus(new TenantId(tenant), TenantStatus.SUSPENDED, "test", "test-suspend");
                assertThat(jdbc.sql("SELECT platform_admin_active(:tenant,:subject)").param("tenant", tenant)
                        .param("subject", second).query(Boolean.class).single()).isFalse();
                tenants.changeStatus(new TenantId(tenant), TenantStatus.ACTIVE, "test", "test-restore");
                assertThat(jdbc.sql("SELECT platform_admin_active(:tenant,:subject)").param("tenant", tenant)
                        .param("subject", second).query(Boolean.class).single()).isFalse();
                assertThatThrownBy(() -> jdbc.sql("SELECT platform_admin_recover(:tenant,:subject,'trace','test')")
                        .param("tenant", tenant).param("subject", second).query(Boolean.class).single())
                        .isInstanceOf(DataAccessException.class);
                try (var recover = owner.prepareStatement("SELECT platform_admin_recover(?,?,?,?)")) {
                    recover.setObject(1, tenant); recover.setObject(2, second);
                    recover.setString(3, "test-recover"); recover.setString(4, "restore after identity outage");
                    try (var result = recover.executeQuery()) { result.next(); assertThat(result.getBoolean(1)).isTrue(); }
                }
                assertThat(jdbc.sql("SELECT platform_admin_active(:tenant,:subject)").param("tenant", tenant)
                        .param("subject", second).query(Boolean.class).single()).isTrue();
                try (var audit = owner.prepareStatement("SELECT operator_db_user, reason FROM platform_admin_audit WHERE target_tenant = ? AND action = 'bootstrap'")) {
                    audit.setObject(1, tenant);
                    try (var result = audit.executeQuery()) { result.next(); assertThat(result.getString(1)).isEqualTo(ownerUser); assertThat(result.getString(2)).isEqualTo("initial setup"); }
                }
                try (var query = owner.prepareStatement("SELECT count(*) FROM platform_admin_audit WHERE target_tenant = ?")) {
                    query.setObject(1, tenant);
                    try (var result = query.executeQuery()) { result.next(); assertThat(result.getLong(1)).isEqualTo(15); }
                }
                owner.setAutoCommit(false);
                try {
                    try (var fault = owner.createStatement()) {
                        fault.execute("CREATE FUNCTION pg_temp.fail_identity_audit() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'injected audit failure'; END $$");
                        fault.execute("CREATE TRIGGER test_identity_audit_failure BEFORE INSERT ON platform_admin_audit FOR EACH ROW EXECUTE FUNCTION pg_temp.fail_identity_audit()");
                    }
                    assertThatThrownBy(() -> {
                        try (var disable = owner.prepareStatement("UPDATE org_connection SET enabled=false WHERE tenant_id=? AND corp_id=?")) {
                            disable.setObject(1, tenant); disable.setString(2, second.toString()); disable.executeUpdate();
                        }
                    }).isInstanceOf(java.sql.SQLException.class);
                } finally {
                    owner.rollback(); owner.setAutoCommit(true);
                }
                assertThat(jdbc.sql("SELECT platform_admin_active(:tenant,:subject)").param("tenant", tenant)
                        .param("subject", second).query(Boolean.class).single()).isTrue();
                try (Connection blocker = DriverManager.getConnection(url, ownerUser, ownerPassword);
                     Connection probe = DriverManager.getConnection(url, ownerUser, ownerPassword);
                     var pool = Executors.newSingleThreadExecutor()) {
                    blocker.setAutoCommit(false);
                    try (var lock = blocker.createStatement()) { lock.execute("SELECT pg_advisory_xact_lock(1979110201)"); }
                    try {
                        var started = new CountDownLatch(1);
                        var update = pool.submit(() -> {
                            try (Connection conn = DriverManager.getConnection(url, ownerUser, ownerPassword)) {
                                try (var scope = conn.prepareStatement("SELECT set_config('app.tenant_id', ?, false)")) {
                                    scope.setString(1, tenant.toString()); scope.execute();
                                }
                                try (var disable = conn.prepareStatement("UPDATE org_connection SET enabled=false WHERE tenant_id=? AND corp_id=?")) {
                                    disable.setObject(1, tenant); disable.setString(2, first.toString());
                                    started.countDown(); return disable.executeUpdate();
                                }
                            }
                        });
                        assertThat(started.await(5, TimeUnit.SECONDS)).isTrue();
                        assertThatThrownBy(() -> update.get(1, TimeUnit.SECONDS)).isInstanceOf(java.util.concurrent.TimeoutException.class);
                        probe.setAutoCommit(false);
                        try (var scope = probe.prepareStatement("SELECT set_config('app.tenant_id', ?, false)")) {
                            scope.setString(1, tenant.toString()); scope.execute();
                        }
                        try (var row = probe.prepareStatement("SELECT id FROM org_connection WHERE tenant_id=? AND corp_id=? FOR UPDATE NOWAIT")) {
                            row.setObject(1, tenant); row.setString(2, first.toString());
                            try (var result = row.executeQuery()) { assertThat(result.next()).isTrue(); }
                        }
                        probe.rollback();
                        blocker.rollback();
                        assertThat(update.get(15, TimeUnit.SECONDS)).isEqualTo(1);
                    } finally { probe.rollback(); blocker.rollback(); }
                }
                try (var enable = owner.prepareStatement("UPDATE org_connection SET enabled=true WHERE tenant_id=? AND corp_id=?")) {
                    enable.setObject(1, tenant); enable.setString(2, first.toString()); enable.executeUpdate();
                }
                var race = new CountDownLatch(1);
                try (var pool = Executors.newFixedThreadPool(2)) {
                    var grant = pool.submit(() -> {
                        try (Connection conn = DriverManager.getConnection(url, ownerUser, ownerPassword)) {
                            race.await();
                            try { return ownerChange(conn, tenant, second, tenant, first, true); }
                            catch (java.sql.SQLException rejected) {
                                assertThat(rejected.getMessage()).contains("target identity unavailable");
                                return false;
                            }
                        }
                    });
                    var disable = pool.submit(() -> {
                        try (Connection conn = DriverManager.getConnection(url, ownerUser, ownerPassword)) {
                            try (var scope = conn.prepareStatement("SELECT set_config('app.tenant_id', ?, false)")) {
                                scope.setString(1, tenant.toString()); scope.execute();
                            }
                            race.await();
                            try (var update = conn.prepareStatement("UPDATE org_connection SET enabled=false WHERE tenant_id=? AND corp_id=?")) {
                                update.setObject(1, tenant); update.setString(2, first.toString());
                                return update.executeUpdate();
                            }
                        }
                    });
                    race.countDown();
                    grant.get(15, TimeUnit.SECONDS);
                    assertThat(disable.get(15, TimeUnit.SECONDS)).isEqualTo(1);
                }
                try (var enable = owner.prepareStatement("UPDATE org_connection SET enabled=true WHERE tenant_id=? AND corp_id=?")) {
                    enable.setObject(1, tenant); enable.setString(2, first.toString()); enable.executeUpdate();
                }
                assertThat(jdbc.sql("SELECT platform_admin_active(:tenant,:subject)").param("tenant", tenant)
                        .param("subject", first).query(Boolean.class).single()).isFalse();
                assertThat(ownerChange(owner, tenant, second, tenant, first, true)).isTrue();
                try (var remove = owner.prepareStatement("UPDATE access_member SET status='removed' WHERE tenant_id=? AND actor_id IN (?,?)")) {
                    remove.setObject(1, tenant); remove.setString(2, first.toString()); remove.setString(3, second.toString());
                    assertThat(remove.executeUpdate()).isEqualTo(2);
                }
                try (var restore = owner.prepareStatement("UPDATE access_member SET status='active' WHERE tenant_id=? AND actor_id IN (?,?)")) {
                    restore.setObject(1, tenant); restore.setString(2, first.toString()); restore.setString(3, second.toString());
                    assertThat(restore.executeUpdate()).isEqualTo(2);
                }
                for (UUID subject : new UUID[] { first, second }) {
                    assertThat(jdbc.sql("SELECT platform_admin_active(:tenant,:subject)").param("tenant", tenant)
                            .param("subject", subject).query(Boolean.class).single()).isFalse();
                }
            } finally {
                try (var cleanup = owner.prepareStatement("DELETE FROM platform_admin_audit WHERE target_tenant = ?")) {
                    cleanup.setObject(1, tenant); cleanup.executeUpdate();
                }
                try (var cleanup = owner.prepareStatement("DELETE FROM platform_admin_grant WHERE identity_tenant = ?")) {
                    cleanup.setObject(1, tenant); cleanup.executeUpdate();
                }
                try (var cleanup = owner.prepareStatement("DELETE FROM identity_binding_revocation WHERE tenant_id = ?")) {
                    cleanup.setObject(1, tenant); cleanup.executeUpdate();
                }
                try (var cleanup = owner.prepareStatement("DELETE FROM org_login_session WHERE tenant_id = ?")) {
                    cleanup.setObject(1, tenant); cleanup.executeUpdate();
                }
                try (var cleanup = owner.prepareStatement("DELETE FROM access_member WHERE tenant_id = ?")) {
                    cleanup.setObject(1, tenant); cleanup.executeUpdate();
                }
                try (var cleanup = owner.prepareStatement("DELETE FROM org_connection WHERE tenant_id = ?")) {
                    cleanup.setObject(1, tenant); cleanup.executeUpdate();
                }
                try (var cleanup = owner.prepareStatement("DELETE FROM identity_binding WHERE tenant_id = ?")) {
                    cleanup.setObject(1, tenant); cleanup.executeUpdate();
                }
            }
        }
    }
    private boolean concurrentSelfRevoke(UUID tenant, UUID actor, CountDownLatch start) throws Exception {
        try (Connection conn = DriverManager.getConnection(url, ownerUser, ownerPassword)) {
            start.await();
            try { return ownerChange(conn, tenant, actor, tenant, actor, false); }
            catch (java.sql.SQLException expected) { return false; }
        }
    }
    private boolean ownerSessionChange(Connection conn, UUID tenant, UUID actor, UUID session, UUID target, boolean grant) throws Exception {
        try (var statement = conn.prepareStatement("SELECT platform_admin_change_session(?,?,?,?,?,?,?,?)")) {
            statement.setObject(1, tenant); statement.setObject(2, actor); statement.setObject(3, session);
            statement.setObject(4, tenant); statement.setObject(5, target); statement.setBoolean(6, grant);
            statement.setString(7, "session-test"); statement.setString(8, "isolated test");
            try (var result = statement.executeQuery()) { result.next(); return result.getBoolean(1); }
        }
    }
    private boolean ownerChange(Connection owner, UUID actorTenant, UUID actor, UUID targetTenant, UUID target, boolean grant) throws Exception {
        try (var statement = owner.prepareStatement("SELECT platform_admin_change(?,?,?,?,?,?,?)")) {
            statement.setObject(1, actorTenant); statement.setObject(2, actor); statement.setObject(3, targetTenant);
            statement.setObject(4, target); statement.setBoolean(5, grant); statement.setString(6, "test-change"); statement.setString(7, "authorized test");
            try (var result = statement.executeQuery()) { result.next(); return result.getBoolean(1); }
        }
    }
    private boolean change(UUID actorTenant, UUID actor, UUID targetTenant, UUID target, boolean grant) {
        return jdbc.sql("SELECT platform_admin_change(:at,:a,:tt,:t,:grant,'test-change','test reason')")
                .param("at", actorTenant).param("a", actor).param("tt", targetTenant).param("t", target)
                .param("grant", grant).query(Boolean.class).single();
    }
}
