package cn.mjy.platform.contacts;

import static org.assertj.core.api.Assertions.assertThat;

import cn.mjy.platform.shared.TenantContext;
import cn.mjy.platform.shared.tenant.TenantScope;
import java.util.UUID;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.jdbc.core.simple.JdbcClient;

/**
 * 导入作业可断点续跑（WP-18 切片 18.1）。进度与每行的写入在同一个事务里推进，
 * 因此进程在任何时刻挂掉，恢复后既不漏行也不重复建人。
 */
@SpringBootTest
class ContactImportResumeTest {

    @Autowired
    private ContactsFixture fixture;

    @Autowired
    private ContactDirectoryService contacts;

    @Autowired
    private ContactImportService imports;

    @Autowired
    private ContactImportWorker worker;

    @Autowired
    private TenantScope tenantScope;

    @Autowired
    private JdbcClient jdbc;

    private static final int ROWS = 7;

    @Test
    void anImportResumesAfterACrashWithoutCreatingDuplicates() {
        TenantContext owner = fixture.newTenant();
        UUID list = fixture.list(owner, "受众", ContactKind.RESPONDENT, DedupeKey.EMAIL).id();
        ImportJobView job = imports.submit(owner, list, new ImportRequest("csv", csv(ROWS)), null);

        // 处理三行后"崩溃"：租约作废，作业还是 running。
        worker.process(owner.tenantId(), job.jobId(), 3);
        assertThat(imports.status(owner, job.jobId()).processedRows()).isEqualTo(3);
        simulateCrash(owner, job.jobId());

        worker.process(owner.tenantId(), job.jobId(), 1000);

        ImportJobView done = imports.status(owner, job.jobId());
        assertThat(done.status()).isEqualTo("completed");
        assertThat(done.processedRows()).isEqualTo(ROWS);
        assertThat(done.created()).isEqualTo(ROWS);
        assertThat(done.duplicate()).isZero();
        assertThat(contacts.search(owner, ContactQuery.all().inList(list).withLimit(100)).items()).hasSize(ROWS);
        assertThat(imports.outcomes(owner, job.jobId(), 1, 100)).hasSize(ROWS);
    }

    @Test
    void replayingARowAlreadyProcessedDoesNotCreateASecondContact() {
        TenantContext owner = fixture.newTenant();
        UUID list = fixture.list(owner, "受众", ContactKind.RESPONDENT, DedupeKey.EMAIL).id();
        ImportJobView job = imports.submit(owner, list, new ImportRequest("csv", csv(ROWS)), null);
        worker.process(owner.tenantId(), job.jobId(), 4);

        // 模拟"写完这一行、还没提交进度就挂了"：把断点退回一行再跑。
        rewindOneRow(owner, job.jobId());
        worker.process(owner.tenantId(), job.jobId(), 1000);

        ImportJobView done = imports.status(owner, job.jobId());
        assertThat(done.status()).isEqualTo("completed");
        assertThat(contacts.search(owner, ContactQuery.all().inList(list).withLimit(100)).items()).hasSize(ROWS);
        assertThat(imports.outcomes(owner, job.jobId(), 1, 100)).hasSize(ROWS);
    }

    @Test
    void aJobHeldByAnotherWorkerIsNotClaimedTwice() {
        TenantContext owner = fixture.newTenant();
        UUID list = fixture.list(owner, "受众", ContactKind.RESPONDENT, DedupeKey.EMAIL).id();
        ImportJobView job = imports.submit(owner, list, new ImportRequest("csv", csv(ROWS)), null);

        holdLease(owner, job.jobId());

        assertThat(worker.process(owner.tenantId(), job.jobId(), 1000)).isFalse();
        assertThat(imports.status(owner, job.jobId()).processedRows()).isZero();
    }

    @Test
    void theRawRowsAreDeletedOnceTheJobFinishes() {
        TenantContext owner = fixture.newTenant();
        UUID list = fixture.list(owner, "受众", ContactKind.RESPONDENT, DedupeKey.EMAIL).id();
        ImportJobView job = imports.submit(owner, list, new ImportRequest("csv", csv(3)), null);

        worker.process(owner.tenantId(), job.jobId(), 1000);

        assertThat(rawRows(owner, job.jobId())).isZero();
    }

    private static String csv(int rows) {
        StringBuilder csv = new StringBuilder("name,email\n");
        for (int i = 1; i <= rows; i++) {
            csv.append("人").append(i).append(",p").append(i).append("@example.com\n");
        }
        return csv.toString();
    }

    private void simulateCrash(TenantContext owner, UUID jobId) {
        tenantScope.run(owner.tenantId(), () -> jdbc.sql(
                        "UPDATE contact_import_job SET lease_token = NULL, lease_until = NULL WHERE id = :id")
                .param("id", jobId).update());
    }

    private void rewindOneRow(TenantContext owner, UUID jobId) {
        tenantScope.run(owner.tenantId(), () -> jdbc.sql("""
                        UPDATE contact_import_job
                           SET next_row = next_row - 1, created_count = created_count - 1,
                               lease_token = NULL, lease_until = NULL
                         WHERE id = :id AND next_row > 1
                        """).param("id", jobId).update());
    }

    private void holdLease(TenantContext owner, UUID jobId) {
        tenantScope.run(owner.tenantId(), () -> jdbc.sql("""
                        UPDATE contact_import_job
                           SET status = 'running', lease_token = gen_random_uuid(),
                               lease_until = now() + interval '10 minutes'
                         WHERE id = :id
                        """).param("id", jobId).update());
    }

    private long rawRows(TenantContext owner, UUID jobId) {
        return tenantScope.call(owner.tenantId(), () -> jdbc
                .sql("SELECT count(*) FROM contact_import_row WHERE job_id = :id")
                .param("id", jobId).query(Long.class).single());
    }
}
