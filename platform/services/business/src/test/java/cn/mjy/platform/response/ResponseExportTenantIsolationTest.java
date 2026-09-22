package cn.mjy.platform.response;

import static cn.mjy.platform.engine.EngineEventType.COMPLETED;
import static cn.mjy.platform.response.ResponseFixture.GENERATION;
import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import cn.mjy.platform.response.ResponseFixture.Published;
import cn.mjy.platform.shared.tenant.TenantScope;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.dao.DataAccessException;
import org.springframework.jdbc.core.simple.JdbcClient;

/**
 * 跨租户：B 拿 A 的作业 id 查状态、下载、取消一律 404；B 不能对 A 的问卷建导出；
 * 裸 SQL 在 B 的作用域里读不到、写不进 A 的作业与快照行；B 的后台处理碰不到 A 的作业。
 */
@SpringBootTest
class ResponseExportTenantIsolationTest {

    @Autowired
    private ExportFixture fixture;

    @Autowired
    private ResponseExportService exports;

    @Autowired
    private ResponseExportWorker worker;

    @Autowired
    private TenantScope tenantScope;

    @Autowired
    private JdbcClient jdbc;

    private Published a;
    private Published b;
    private ExportJobView jobOfA;

    @BeforeEach
    void twoTenantsAndACompletedExportInA() {
        a = fixture.responses().publishedSurvey();
        b = fixture.responses().publishedSurvey();
        fixture.responses().response(a.tenant(), a.instance(), a.sid(), GENERATION, 1, COMPLETED);
        jobOfA = fixture.create(a.owner(), a, "csv");
    }

    @Test
    void anotherTenantGets404OnEveryJobEndpoint() {
        fixture.runToEnd(a.owner(), jobOfA);

        assertThatThrownBy(() -> exports.status(b.owner(), jobOfA.jobId()))
                .isInstanceOf(ResponseNotFoundException.class);
        assertThatThrownBy(() -> exports.download(b.owner(), jobOfA.jobId()))
                .isInstanceOf(ResponseNotFoundException.class);
        assertThatThrownBy(() -> exports.cancel(b.owner(), jobOfA.jobId()))
                .isInstanceOf(ResponseNotFoundException.class);
        assertThatThrownBy(() -> fixture.create(b.owner(), a, "csv"))
                .isInstanceOf(ResponseNotFoundException.class);
    }

    @Test
    void theWorkerUnderAnotherTenantCannotClaimTheJob() {
        assertThat(worker.process(b.tenant(), jobOfA.jobId(), Integer.MAX_VALUE)).isFalse();
        assertThat(exports.status(a.owner(), jobOfA.jobId()).status()).isEqualTo("queued");
    }

    @Test
    void rawSqlCannotReadOrWriteAnotherTenantsJobsOrSnapshotRows() {
        fixture.runToEnd(a.owner(), fixture.create(a.owner(), a, "csv"));
        worker.process(a.tenant(), jobOfA.jobId(), 1);

        long itemsInA = tenantScope.call(a.tenant(), () -> jdbc
                .sql("SELECT COUNT(*) FROM response_export_item WHERE job_id = :j")
                .param("j", jobOfA.jobId()).query(Long.class).single());
        long jobsSeenByB = tenantScope.call(b.tenant(), () -> jdbc
                .sql("SELECT COUNT(*) FROM response_export_job WHERE id = :j")
                .param("j", jobOfA.jobId()).query(Long.class).single());
        long itemsSeenByB = tenantScope.call(b.tenant(), () -> jdbc
                .sql("SELECT COUNT(*) FROM response_export_item WHERE job_id = :j")
                .param("j", jobOfA.jobId()).query(Long.class).single());
        int updatedByB = tenantScope.call(b.tenant(), () -> jdbc
                .sql("UPDATE response_export_job SET status = 'cancelled' WHERE id = :j")
                .param("j", jobOfA.jobId()).update());

        assertThat(itemsInA).isEqualTo(1);
        assertThat(jobsSeenByB).isZero();
        assertThat(itemsSeenByB).isZero();
        assertThat(updatedByB).isZero();
        assertThatThrownBy(() -> tenantScope.run(b.tenant(), () -> jdbc.sql("""
                        INSERT INTO response_export_item (tenant_id, job_id, seq, version_no, engine_instance_id,
                            engine_sid, generation, response_id, state, first_event_at)
                        VALUES (:t, :j, 99, 1, 'x', 1, 'g', 1, 'in_progress', now())
                        """)
                .param("t", a.tenant().value()).param("j", jobOfA.jobId()).update()))
                .isInstanceOf(DataAccessException.class);
        assertThatThrownBy(() -> tenantScope.run(b.tenant(), () -> jdbc.sql("""
                        INSERT INTO response_export_job (tenant_id, id, survey_id, requested_by, format,
                            filter_snapshot, plan, reveal_sensitive, expires_at)
                        VALUES (:t, gen_random_uuid(), gen_random_uuid(), 'x', 'csv', '{}', '{}', false,
                            now() + interval '1 day')
                        """)
                .param("t", a.tenant().value()).update()))
                .isInstanceOf(DataAccessException.class);
    }

    @Test
    void theRuntimeAccountCannotDeleteJobs() {
        assertThatThrownBy(() -> tenantScope.run(a.tenant(), () -> jdbc
                        .sql("DELETE FROM response_export_job WHERE id = :j")
                        .param("j", jobOfA.jobId()).update()))
                .isInstanceOf(DataAccessException.class);
    }
}
