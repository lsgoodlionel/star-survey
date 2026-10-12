package cn.mjy.platform.access;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import cn.mjy.platform.shared.TenantContext;
import cn.mjy.platform.shared.TenantId;
import cn.mjy.platform.shared.tenant.TenantScope;
import java.io.InputStream;
import java.nio.charset.StandardCharsets;
import java.sql.Connection;
import java.sql.DriverManager;
import java.sql.ResultSet;
import java.sql.Statement;
import java.time.Instant;
import java.util.UUID;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.ValueSource;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.jdbc.core.simple.JdbcClient;

/**
 * 跨租户负面测试，覆盖 access 模块的每一张租户表。故意用"写错租户"的原始 SQL 模拟代码缺陷，
 * 证明拦截来自 PostgreSQL 行级安全与带租户的复合外键，而不只是业务代码。
 */
@SpringBootTest
class AccessTenantIsolationTest {

    @Autowired
    private AccessFixture fixture;

    @Autowired
    private AccessDecisionService access;

    @Autowired
    private GrantService grants;

    @Autowired
    private AccessSettingsService settings;

    @Autowired
    private TenantScope tenantScope;

    @Autowired
    private JdbcClient jdbc;

    @Value("${spring.flyway.url}")
    private String flywayUrl;

    @Value("${spring.flyway.user}")
    private String flywayUser;

    @Value("${spring.flyway.password}")
    private String flywayPassword;

    private TenantContext ownerA;
    private TenantContext ownerB;
    private TenantId tenantA;
    private TenantId tenantB;
    private UUID surveyA;

    @BeforeEach
    void twoTenants() {
        ownerA = fixture.newTenant();
        ownerB = fixture.newTenant();
        tenantA = ownerA.tenantId();
        tenantB = ownerB.tenantId();
        surveyA = fixture.survey(tenantA, fixture.project(tenantA));
        fixture.member(ownerA, "editor-a", "editor", surveyA);
        settings.setPublishApprovalRequired(ownerA, false);
    }

    @ParameterizedTest
    @ValueSource(strings = {"access_resource", "access_member", "access_grant", "access_tenant_settings"})
    void tenantBCannotReadTenantARowsEvenWhenAskingForThemExplicitly(String table) {
        long seenByA = countFor(tenantA, table);
        long seenByB = countFor(tenantB, table);

        assertThat(seenByA).isPositive();
        assertThat(seenByB).isZero();
    }

    @ParameterizedTest
    @ValueSource(strings = {"access_resource", "access_member", "access_grant", "access_tenant_settings"})
    void tenantBCannotUpdateOrDeleteTenantARows(String table) {
        long before = countFor(tenantA, table);

        int updated = tenantScope.call(tenantB, () -> jdbc.sql(
                "UPDATE " + table + " SET tenant_id = tenant_id WHERE tenant_id = :a").param("a", tenantA.value()).update());
        int deleted = tenantScope.call(tenantB, () -> jdbc.sql(
                "DELETE FROM " + table + " WHERE tenant_id = :a").param("a", tenantA.value()).update());

        assertThat(updated).isZero();
        assertThat(deleted).isZero();
        assertThat(countFor(tenantA, table)).isEqualTo(before);
    }

    @Test
    void tenantBCannotInsertAResourceIntoTenantA() {
        assertRejectedByRowLevelSecurity(() -> jdbc.sql("""
                INSERT INTO access_resource (tenant_id, id, kind, parent_id, name)
                VALUES (:a, :id, 'project', NULL, 'x')
                """).param("a", tenantA.value()).param("id", UUID.randomUUID()).update());
    }

    @Test
    void tenantBCannotInsertAMemberIntoTenantA() {
        assertRejectedByRowLevelSecurity(() -> jdbc.sql("""
                INSERT INTO access_member (tenant_id, actor_id, status, uses_seat, invited_by)
                VALUES (:a, 'intruder', 'active', true, 'intruder')
                """).param("a", tenantA.value()).update());
    }

    @Test
    void tenantBCannotInsertAGrantIntoTenantA() {
        assertRejectedByRowLevelSecurity(() -> jdbc.sql("""
                INSERT INTO access_grant (tenant_id, id, actor_id, role_code, resource_id, granted_by)
                VALUES (:a, :id, 'editor-a', 'tenant_owner', NULL, 'intruder')
                """).param("a", tenantA.value()).param("id", UUID.randomUUID()).update());
    }

    @Test
    void tenantBCannotChangeTenantASettings() {
        assertRejectedByRowLevelSecurity(() -> jdbc.sql("""
                INSERT INTO access_tenant_settings (tenant_id, publish_approval_required, updated_by)
                VALUES (:a, false, 'intruder')
                """).param("a", tenantA.value()).update());
    }

    @Test
    void aGrantInTenantBCannotPointAtTenantAResource() {
        fixture.activeMember(ownerB, "editor-b");

        assertThatThrownBy(() -> tenantScope.run(tenantB, () -> jdbc.sql("""
                INSERT INTO access_grant (tenant_id, id, actor_id, role_code, resource_id, granted_by)
                VALUES (:b, :id, 'editor-b', 'editor', :survey, 'owner')
                """).param("b", tenantB.value()).param("id", UUID.randomUUID()).param("survey", surveyA).update()))
                .rootCause().hasMessageContaining("foreign key");
    }

    @Test
    void tenantBOwnerCannotGrantOnTenantAResourceThroughTheService() {
        fixture.activeMember(ownerB, "editor-b");

        assertThatThrownBy(() -> grants.grant(ownerB, new GrantRequest("editor-b", "editor", surveyA, null)))
                .isInstanceOf(IllegalArgumentException.class);
        assertThatThrownBy(() -> grants.grant(ownerB, new GrantRequest("editor-a", "editor", null, null)))
                .isInstanceOf(IllegalArgumentException.class);
    }

    @Test
    void tenantBOwnerSeesTenantAResourceAsUnknown() {
        Decision decision = access.can(ownerB, Permission.VIEW, surveyA);

        assertThat(decision.allowed()).isFalse();
        assertThat(decision.reason()).isEqualTo(DecisionReason.UNKNOWN_RESOURCE);
    }

    @Test
    void tenantBOwnerCannotListTenantAGrants() {
        assertThat(grants.list(ownerB)).noneMatch(g -> g.actorId().equals("editor-a"));
    }

    @Test
    void tenantASettingsDoNotAffectTenantB() {
        assertThat(settings.isPublishApprovalRequired(tenantA)).isFalse();
        assertThat(settings.isPublishApprovalRequired(tenantB)).isTrue();
    }

    @Test
    void migrationBackfillsUpdatedAtFromCreatedAt() throws Exception {
        String schema = "resource_migration_" + UUID.randomUUID().toString().replace("-", "");
        Instant created = Instant.parse("2020-02-03T04:05:06Z");
        try (Connection connection = DriverManager.getConnection(flywayUrl, flywayUser, flywayPassword);
             Statement statement = connection.createStatement()) {
            statement.execute("CREATE SCHEMA " + schema);
            try {
                statement.execute("SET search_path TO " + schema);
                statement.execute("""
                        CREATE TABLE access_resource (
                            tenant_id uuid NOT NULL,
                            id uuid NOT NULL,
                            kind text NOT NULL,
                            parent_id uuid,
                            name text NOT NULL,
                            created_at timestamptz NOT NULL DEFAULT now(),
                            PRIMARY KEY (tenant_id, id)
                        )
                        """);
                statement.execute("INSERT INTO access_resource (tenant_id, id, kind, name, created_at) VALUES ("
                        + "'00000000-0000-0000-0000-000000000001', "
                        + "'00000000-0000-0000-0000-000000000002', 'project', '历史项目', '" + created + "')");
                try (InputStream migration = getClass().getResourceAsStream(
                        "/db/migration/V403__access_resource_workspace.sql")) {
                    assertThat(migration).isNotNull();
                    statement.execute(new String(migration.readAllBytes(), StandardCharsets.UTF_8));
                }
                try (ResultSet row = statement.executeQuery(
                        "SELECT created_at, updated_at, archived_at FROM access_resource")) {
                    assertThat(row.next()).isTrue();
                    assertThat(row.getTimestamp("updated_at").toInstant()).isEqualTo(created);
                    assertThat(row.getTimestamp("created_at").toInstant()).isEqualTo(created);
                    assertThat(row.getTimestamp("archived_at")).isNull();
                }
            } finally {
                statement.execute("DROP SCHEMA " + schema + " CASCADE");
            }
        }
    }

    private long countFor(TenantId viewer, String table) {
        return tenantScope.call(viewer, () -> jdbc.sql("SELECT COUNT(*) FROM " + table + " WHERE tenant_id = :a")
                .param("a", tenantA.value()).query(Long.class).single());
    }

    private void assertRejectedByRowLevelSecurity(Runnable write) {
        assertThatThrownBy(() -> tenantScope.run(tenantB, write)).rootCause().hasMessageContaining("row-level security");
    }
}
