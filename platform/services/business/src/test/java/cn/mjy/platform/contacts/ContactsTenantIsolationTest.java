package cn.mjy.platform.contacts;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import cn.mjy.platform.shared.TenantContext;
import cn.mjy.platform.shared.TenantId;
import cn.mjy.platform.shared.tenant.TenantScope;
import java.util.UUID;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.ValueSource;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.jdbc.core.simple.JdbcClient;

/**
 * 通讯录全部新表的跨租户负面测试（CONVENTIONS.md 硬性要求）：直接写裸 SQL，指名别的租户的
 * tenant_id，由数据库（行级安全 / 复合外键 / 最小授权）拒绝，而不是靠服务层判断。
 */
@SpringBootTest
class ContactsTenantIsolationTest {

    /** 本车道新增的全部租户表。 */
    private static final String[] TABLES = {
            "contact_list", "contact_org_unit", "contact", "contact_tag", "contact_tag_assignment",
            "contact_link", "contact_scope_grant", "contact_import_job", "contact_import_row",
            "contact_import_outcome", "contact_participation",
    };

    @Autowired
    private ContactsFixture fixture;

    @Autowired
    private ContactDirectoryService contacts;

    @Autowired
    private ContactTagService tags;

    @Autowired
    private ContactScopeService scopes;

    @Autowired
    private ContactImportService imports;

    @Autowired
    private ContactImportWorker worker;

    @Autowired
    private TenantScope tenantScope;

    @Autowired
    private JdbcClient jdbc;

    private TenantContext ownerA;
    private TenantContext ownerB;
    private TenantId tenantA;
    private TenantId tenantB;
    private UUID listA;
    private UUID unitA;
    private ContactView contactA;

    @BeforeEach
    void seed() {
        ownerA = fixture.newTenant();
        tenantA = ownerA.tenantId();
        ownerB = fixture.newTenant();
        tenantB = ownerB.tenantId();
        listA = fixture.list(ownerA, "受众", ContactKind.RESPONDENT, DedupeKey.EMAIL).id();
        unitA = fixture.unit(ownerA, null, "A 部").id();
        contactA = contacts.create(ownerA, listA, ContactKind.RESPONDENT,
                ContactDraft.named("甲").withEmail("ann@example.com").withUnit(unitA));
        ContactView other = contacts.create(ownerA, listA, ContactKind.RESPONDENT,
                ContactDraft.named("乙").withEmail("bob@example.com").withUnit(unitA));
        contacts.link(ownerA, contactA.id(), other.id(), "同一人");
        ContactTagView tag = tags.create(ownerA, "VIP");
        tags.assign(ownerA, contactA.id(), tag.id());
        scopes.grant(ownerA, "someone", unitA, null);
        ImportJobView job = imports.submit(ownerA, listA,
                new ImportRequest("csv", "name,email\n丙,carl@example.com\n"), null);
        worker.process(tenantA, job.jobId(), 10);
        seedParticipation();
    }

    @ParameterizedTest
    @ValueSource(strings = {
            "contact_list", "contact_org_unit", "contact", "contact_tag", "contact_tag_assignment",
            "contact_link", "contact_scope_grant", "contact_import_job", "contact_import_outcome",
            "contact_participation",
    })
    void tenantBSeesNoneOfTenantAsRows(String table) {
        assertThat(countFor(tenantA, table)).isPositive();
        assertThat(countFor(tenantB, table)).isZero();
        assertThat(updateAll(tenantB, table)).isZero();
    }

    @Test
    void withoutATenantContextNothingIsVisible() {
        for (String table : TABLES) {
            assertThat(jdbc.sql("SELECT count(*) FROM " + table).query(Long.class).single())
                    .as(table).isZero();
        }
    }

    @Test
    void tenantBCannotInsertARowIntoTenantA() {
        assertRejectedByRowLevelSecurity(() -> jdbc.sql("""
                INSERT INTO contact_list (tenant_id, id, name, kind, dedupe_key, created_by)
                VALUES (:a, :id, '偷来的名单', 'respondent', 'email', 'attacker')
                """).param("a", tenantA.value()).param("id", UUID.randomUUID()).update());
        assertRejectedByRowLevelSecurity(() -> jdbc.sql("""
                INSERT INTO contact_org_unit (tenant_id, id, parent_id, name, created_by)
                VALUES (:a, :id, NULL, '偷来的部门', 'attacker')
                """).param("a", tenantA.value()).param("id", UUID.randomUUID()).update());
        assertRejectedByRowLevelSecurity(() -> jdbc.sql("""
                INSERT INTO contact (tenant_id, id, kind, source, dedupe_value, created_by)
                VALUES (:a, :id, 'respondent', 'manual', 'x@example.com', 'attacker')
                """).param("a", tenantA.value()).param("id", UUID.randomUUID()).update());
        assertRejectedByRowLevelSecurity(() -> jdbc.sql("""
                INSERT INTO contact_tag (tenant_id, id, name, created_by)
                VALUES (:a, :id, '偷来的标签', 'attacker')
                """).param("a", tenantA.value()).param("id", UUID.randomUUID()).update());
    }

    @Test
    void aContactInTenantBCannotPointAtTenantAsListOrUnit() {
        assertThatThrownBy(() -> tenantScope.run(tenantB, () -> jdbc.sql("""
                INSERT INTO contact (tenant_id, id, kind, list_id, source, dedupe_value, created_by)
                VALUES (:b, :id, 'respondent', :list, 'manual', 'x@example.com', 'attacker')
                """).param("b", tenantB.value()).param("id", UUID.randomUUID()).param("list", listA).update()))
                .rootCause().hasMessageContaining("foreign key");

        assertThatThrownBy(() -> tenantScope.run(tenantB, () -> jdbc.sql("""
                INSERT INTO contact (tenant_id, id, kind, org_unit_id, source, dedupe_value, created_by)
                VALUES (:b, :id, 'respondent', :unit, 'manual', 'y@example.com', 'attacker')
                """).param("b", tenantB.value()).param("id", UUID.randomUUID()).param("unit", unitA).update()))
                .rootCause().hasMessageContaining("foreign key");
    }

    @Test
    void aScopeGrantInTenantBCannotPointAtTenantAsUnit() {
        assertThatThrownBy(() -> tenantScope.run(tenantB, () -> jdbc.sql("""
                INSERT INTO contact_scope_grant (tenant_id, id, actor_id, org_unit_id, granted_by)
                VALUES (:b, :id, 'attacker', :unit, 'attacker')
                """).param("b", tenantB.value()).param("id", UUID.randomUUID()).param("unit", unitA).update()))
                .rootCause().hasMessageContaining("foreign key");
    }

    @Test
    void contactsAndLinksCannotBeDeletedByTheRuntimeAccount() {
        assertPermissionDenied(() -> jdbc.sql("DELETE FROM contact").update());
        assertPermissionDenied(() -> jdbc.sql("DELETE FROM contact_list").update());
        assertPermissionDenied(() -> jdbc.sql("DELETE FROM contact_org_unit").update());
        assertPermissionDenied(() -> jdbc.sql("DELETE FROM contact_link").update());
        assertPermissionDenied(() -> jdbc.sql("DELETE FROM contact_import_outcome").update());
        assertPermissionDenied(() -> jdbc.sql("UPDATE contact_import_outcome SET outcome = 'created'").update());
        assertPermissionDenied(() -> jdbc.sql("UPDATE contact_participation SET participant_token = 'x'").update());
    }

    @Test
    void crossTenantReadsFromTheServiceAre404() {
        assertThatThrownBy(() -> contacts.get(ownerB, contactA.id()))
                .isInstanceOf(ContactNotFoundException.class);
        assertThat(contacts.search(ownerB, ContactQuery.all()).items()).isEmpty();
    }

    private void seedParticipation() {
        tenantScope.run(tenantA, () -> jdbc.sql("""
                INSERT INTO contact_participation (tenant_id, id, contact_id, survey_id, version_no,
                    engine_instance_id, engine_sid, participant_token, issued_by)
                VALUES (:a, :id, :contact, :survey, 1, 'engine-iso', 4242, 'tok-isolation-1', 'owner')
                """).param("a", tenantA.value()).param("id", UUID.randomUUID())
                .param("contact", contactA.id()).param("survey", UUID.randomUUID()).update());
    }

    private long countFor(TenantId viewer, String table) {
        return tenantScope.call(viewer, () -> jdbc.sql("SELECT count(*) FROM " + table).query(Long.class).single());
    }

    private int updateAll(TenantId viewer, String table) {
        String column = switch (table) {
            case "contact_import_outcome", "contact_participation" -> null;
            case "contact_tag_assignment" -> "assigned_by";
            case "contact_import_job" -> "last_error";
            case "contact_link" -> "unlinked_by";
            case "contact_scope_grant" -> "granted_by";
            default -> "created_by";
        };
        if (column == null) {
            return 0;
        }
        return tenantScope.call(viewer,
                () -> jdbc.sql("UPDATE " + table + " SET " + column + " = 'hijacked'").update());
    }

    private void assertRejectedByRowLevelSecurity(Runnable write) {
        assertThatThrownBy(() -> tenantScope.run(tenantB, write)).rootCause()
                .hasMessageContaining("row-level security");
    }

    private void assertPermissionDenied(Runnable write) {
        assertThatThrownBy(() -> tenantScope.run(tenantA, write)).rootCause()
                .hasMessageContaining("permission denied");
    }
}
