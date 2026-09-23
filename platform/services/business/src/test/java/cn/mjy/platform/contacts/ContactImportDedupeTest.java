package cn.mjy.platform.contacts;

import static org.assertj.core.api.Assertions.assertThat;

import cn.mjy.platform.shared.TenantContext;
import java.util.List;
import java.util.UUID;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.EnumSource;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;

/**
 * 导入与去重（WP-18 切片 18.1，R18-01："全量增量同步及错误行反馈准确"）。
 * 每行独立判定，坏行只记结果不终止作业；去重键由名单在创建时选定。
 */
@SpringBootTest
class ContactImportDedupeTest {

    @Autowired
    private ContactsFixture fixture;

    @Autowired
    private ContactDirectoryService contacts;

    @Autowired
    private ContactImportService imports;

    @Autowired
    private ContactImportWorker worker;

    /** 按去重键给出"同一个人"的两次写法（大小写与分隔符不同），以及另一个人。 */
    private record Sample(ContactDraft first, ContactDraft sameAgain, ContactDraft other) {
    }

    private static Sample sampleFor(DedupeKey key) {
        return switch (key) {
            case EMAIL -> new Sample(
                    ContactDraft.named("甲").withEmail("Ann@Example.com"),
                    ContactDraft.named("甲二").withEmail("ann@example.com"),
                    ContactDraft.named("乙").withEmail("bob@example.com"));
            case PHONE -> new Sample(
                    ContactDraft.named("甲").withPhone("+86 138-0000-0001"),
                    ContactDraft.named("甲二").withPhone("+8613800000001"),
                    ContactDraft.named("乙").withPhone("+8613800000002"));
            case EMPLOYEE_ID -> new Sample(
                    ContactDraft.named("甲").withEmployeeId("E-001"),
                    ContactDraft.named("甲二").withEmployeeId("E-001"),
                    ContactDraft.named("乙").withEmployeeId("E-002"));
            case EXTERNAL_ID -> new Sample(
                    ContactDraft.named("甲").withIdentity(new ExternalContactIdentity("wecom", "app-1", "open-1")),
                    ContactDraft.named("甲二").withIdentity(new ExternalContactIdentity("wecom", "app-1", "open-1")),
                    ContactDraft.named("乙").withIdentity(new ExternalContactIdentity("wecom", "app-1", "open-2")));
        };
    }

    @ParameterizedTest
    @EnumSource(DedupeKey.class)
    void importDedupesAgainstExistingContactsAndWithinTheFileByTheConfiguredKey(DedupeKey key) {
        TenantContext owner = fixture.newTenant();
        UUID list = fixture.list(owner, "名单-" + key.code(), ContactKind.RESPONDENT, key).id();
        Sample sample = sampleFor(key);
        contacts.create(owner, list, ContactKind.RESPONDENT, sample.first());

        ImportJobView job = run(owner, list, List.of(
                sample.sameAgain(),   // 命中库里已有的 → updated
                sample.other(),       // 新人 → created
                sample.other()));     // 文件内重复 → duplicate

        assertThat(job.status()).isEqualTo("completed");
        assertThat(job.created()).isEqualTo(1);
        assertThat(job.updated()).isEqualTo(1);
        assertThat(job.duplicate()).isEqualTo(1);
        assertThat(job.invalid()).isZero();
        assertThat(contacts.search(owner, ContactQuery.all().inList(list)).items()).hasSize(2);

        List<ImportRowOutcome> outcomes = imports.outcomes(owner, job.jobId(), 1, 50);
        assertThat(outcomes).extracting(ImportRowOutcome::outcome)
                .containsExactly("updated", "created", "duplicate");
        assertThat(outcomes.get(2).reason()).isEqualTo("duplicate_in_file");
    }

    @Test
    void theSameOpenIdUnderTwoAppsIsImportedAsTwoContacts() {
        TenantContext owner = fixture.newTenant();
        UUID list = fixture.list(owner, "微信受众", ContactKind.RESPONDENT, DedupeKey.EXTERNAL_ID).id();

        ImportJobView job = run(owner, list, List.of(
                ContactDraft.named("甲").withIdentity(new ExternalContactIdentity("wecom", "app-1", "open-x")),
                ContactDraft.named("甲").withIdentity(new ExternalContactIdentity("wecom", "app-2", "open-x"))));

        assertThat(job.created()).isEqualTo(2);
        assertThat(job.duplicate()).isZero();
        assertThat(contacts.search(owner, ContactQuery.all().inList(list)).items()).hasSize(2);
    }

    @Test
    void invalidRowsAreReportedWithoutAbortingTheImport() {
        TenantContext owner = fixture.newTenant();
        UUID list = fixture.list(owner, "受众", ContactKind.RESPONDENT, DedupeKey.EMAIL).id();

        String csv = """
                name,email
                甲,ann@example.com
                乙,not-an-email
                丙,
                丁,dan@example.com
                """;
        ImportJobView job = runCsv(owner, list, csv);

        assertThat(job.status()).isEqualTo("completed");
        assertThat(job.created()).isEqualTo(2);
        assertThat(job.invalid()).isEqualTo(2);
        List<ImportRowOutcome> outcomes = imports.outcomes(owner, job.jobId(), 1, 50);
        assertThat(outcomes).extracting(ImportRowOutcome::outcome)
                .containsExactly("created", "invalid", "invalid", "created");
        assertThat(outcomes.get(1).reason()).isEqualTo("invalid_email");
        assertThat(outcomes.get(2).reason()).isEqualTo("missing_dedupe_key");
        // 坏行不写联系人 id，报告里也不含行内容（不泄漏个人信息）。
        assertThat(outcomes.get(1).contactId()).isNull();
    }

    @Test
    void aRowPointingAtAnUnknownOrgUnitIsInvalidNotFatal() {
        TenantContext owner = fixture.newTenant();
        UUID list = fixture.list(owner, "受众", ContactKind.RESPONDENT, DedupeKey.EMAIL).id();
        fixture.unitWithRef(owner, null, "研发部", "D001");

        String csv = """
                name,email,org_unit
                甲,ann@example.com,D001
                乙,bob@example.com,D999
                """;
        ImportJobView job = runCsv(owner, list, csv);

        assertThat(job.created()).isEqualTo(1);
        assertThat(job.invalid()).isEqualTo(1);
        assertThat(imports.outcomes(owner, job.jobId(), 1, 50).get(1).reason()).isEqualTo("unknown_org_unit");
    }

    @Test
    void jsonImportsFollowTheSameRules() {
        TenantContext owner = fixture.newTenant();
        UUID list = fixture.list(owner, "受众", ContactKind.RESPONDENT, DedupeKey.PHONE).id();

        String body = """
                [{"name":"甲","phone":"13800000001"},
                 {"name":"甲重复","phone":"138 0000 0001"},
                 {"name":"乙","phone":"oops"}]
                """;
        ImportJobView job = imports.submit(owner, list, new ImportRequest("json", body), null);
        worker.process(owner.tenantId(), job.jobId(), 100);
        ImportJobView done = imports.status(owner, job.jobId());

        assertThat(done.created()).isEqualTo(1);
        assertThat(done.duplicate()).isEqualTo(1);
        assertThat(done.invalid()).isEqualTo(1);
    }

    @Test
    void submittingTheSameIdempotencyKeyTwiceReturnsTheSameJob() {
        TenantContext owner = fixture.newTenant();
        UUID list = fixture.list(owner, "受众", ContactKind.RESPONDENT, DedupeKey.EMAIL).id();
        ImportRequest request = new ImportRequest("csv", "name,email\n甲,ann@example.com\n");

        ImportJobView first = imports.submit(owner, list, request, "batch-1");
        ImportJobView again = imports.submit(owner, list, request, "batch-1");

        assertThat(again.jobId()).isEqualTo(first.jobId());
    }

    private ImportJobView run(TenantContext owner, UUID list, List<ContactDraft> rows) {
        return runCsv(owner, list, csvOf(rows));
    }

    private ImportJobView runCsv(TenantContext owner, UUID list, String csv) {
        ImportJobView job = imports.submit(owner, list, new ImportRequest("csv", csv), null);
        worker.process(owner.tenantId(), job.jobId(), 1000);
        return imports.status(owner, job.jobId());
    }

    private static String csvOf(List<ContactDraft> rows) {
        StringBuilder csv = new StringBuilder("name,email,phone,employee_id,provider,app_id,external_id\n");
        for (ContactDraft row : rows) {
            ExternalContactIdentity id = row.identity();
            csv.append(nullToEmpty(row.displayName())).append(',')
                    .append(nullToEmpty(row.email())).append(',')
                    .append(nullToEmpty(row.phone())).append(',')
                    .append(nullToEmpty(row.employeeId())).append(',')
                    .append(id == null ? "" : id.provider()).append(',')
                    .append(id == null ? "" : id.appId()).append(',')
                    .append(id == null ? "" : id.externalId()).append('\n');
        }
        return csv.toString();
    }

    private static String nullToEmpty(String value) {
        return value == null ? "" : value;
    }
}
