package cn.mjy.platform.response;

import static cn.mjy.platform.engine.EngineEventType.COMPLETED;
import static cn.mjy.platform.response.ResponseFixture.GENERATION;
import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import cn.mjy.platform.engine.ResponseProjectionQuery;
import cn.mjy.platform.response.ResponseFixture.Published;
import cn.mjy.platform.shared.tenant.TenantScope;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.dao.DataAccessException;
import org.springframework.jdbc.core.simple.JdbcClient;

/**
 * 跨租户：B 用 A 的问卷 id 一律 404、网关一次都不被调用；即使 B 知道 A 的 (实例, sid)，
 * 在 B 的作用域里按键查投影也一行都看不到，裸 SQL 写入 A 的投影被行级安全拒绝。
 */
@SpringBootTest
class ResponseTenantIsolationTest {

    @Autowired
    private ResponseFixture fixture;

    @Autowired
    private ResponseQueryService responses;

    @Autowired
    private ResponseProjectionQuery projections;

    @Autowired
    private FakeResponseAnswerSource answers;

    @Autowired
    private TenantScope tenantScope;

    @Autowired
    private JdbcClient jdbc;

    private Published a;
    private Published b;

    @BeforeEach
    void twoTenantsEachWithAPublishedSurvey() {
        a = fixture.publishedSurvey();
        b = fixture.publishedSurvey();
        fixture.response(a.tenant(), a.instance(), a.sid(), GENERATION, 1, COMPLETED);
    }

    @Test
    void anotherTenantGets404OnListSummaryAndFields() {
        assertThatThrownBy(() -> responses.list(b.owner(), a.surveyId(), null, null, 10))
                .isInstanceOf(ResponseNotFoundException.class);
        assertThatThrownBy(() -> responses.summary(b.owner(), a.surveyId()))
                .isInstanceOf(ResponseNotFoundException.class);
        assertThatThrownBy(() -> responses.fields(b.owner(), a.surveyId()))
                .isInstanceOf(ResponseNotFoundException.class);
        assertThat(answers.callsFor(a.instance())).isEmpty();
    }

    @Test
    void theProjectionQueryUnderAnotherTenantsScopeSeesNothing() {
        var inA = tenantScope.call(a.tenant(),
                () -> projections.page(a.instance(), a.sid(), null, null, 10));
        var inB = tenantScope.call(b.tenant(),
                () -> projections.page(a.instance(), a.sid(), null, null, 10));

        assertThat(inA).hasSize(1);
        assertThat(inB).isEmpty();
        assertThat(tenantScope.call(b.tenant(), () -> projections.countByState(a.instance(), a.sid()))).isEmpty();
        assertThat(tenantScope.call(b.tenant(), () -> projections.latestGeneration(a.instance(), a.sid()))).isEmpty();
    }

    @Test
    void rawSqlCannotReadOrWriteAnotherTenantsProjection() {
        long visible = tenantScope.call(b.tenant(), () -> jdbc
                .sql("SELECT COUNT(*) FROM response_projection WHERE engine_instance_id = :i AND survey_id = :s")
                .param("i", a.instance()).param("s", a.sid()).query(Long.class).single());
        assertThat(visible).isZero();

        assertThatThrownBy(() -> tenantScope.run(b.tenant(), () -> jdbc.sql("""
                        INSERT INTO response_projection (tenant_id, engine_instance_id, survey_id, generation,
                            response_id, state, first_event_at, last_event_id)
                        VALUES (:t, :i, :s, 'g', 99, 'in_progress', now(), gen_random_uuid())
                        """)
                .param("t", a.tenant().value()).param("i", a.instance()).param("s", a.sid()).update()))
                .isInstanceOf(DataAccessException.class);
    }
}
