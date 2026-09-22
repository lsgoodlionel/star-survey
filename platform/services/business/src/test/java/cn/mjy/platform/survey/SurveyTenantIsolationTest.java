package cn.mjy.platform.survey;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import cn.mjy.platform.shared.TenantId;
import cn.mjy.platform.shared.tenant.TenantScope;
import cn.mjy.platform.survey.SurveyFixture.Workspace;
import java.util.UUID;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.jdbc.core.simple.JdbcClient;

/**
 * 问卷模块的四张租户表都由数据库行级安全隔离：B 读不到 A 的行，B 以 A 的 tenant_id 写入被数据库拒绝，
 * 已发布版本与题目映射连本租户都改不了（只追加）。这里故意绕过服务层直接写 SQL，证明挡住的是数据库而不是代码。
 */
@SpringBootTest
class SurveyTenantIsolationTest {

    private static final String[] TABLES = {
        "survey", "survey_publish_attempt", "survey_published_version", "survey_question_binding"};

    @Autowired
    private SurveyFixture fixture;

    @Autowired
    private SurveyPublishService publisher;

    @Autowired
    private TenantScope tenantScope;

    @Autowired
    private JdbcClient jdbc;

    private Workspace a;
    private Workspace b;
    private UUID surveyOfA;

    @BeforeEach
    void tenantAHasAPublishedSurvey() {
        a = fixture.workspace();
        b = fixture.workspace();
        surveyOfA = fixture.newSurvey(a).id();
        publisher.publish(a.owner(), surveyOfA);
    }

    private long rowsOfSurveyA(TenantId scope, String table) {
        String column = table.equals("survey") ? "id" : "survey_id";
        return tenantScope.call(scope, () -> jdbc
                .sql("SELECT COUNT(*) FROM " + table + " WHERE " + column + " = :id")
                .param("id", surveyOfA)
                .query(Long.class).single());
    }

    @Test
    void tenantBSeesNoneOfTenantAsRowsInAnySurveyTable() {
        for (String table : TABLES) {
            assertThat(rowsOfSurveyA(a.tenant(), table)).as("A sees own " + table).isPositive();
            assertThat(rowsOfSurveyA(b.tenant(), table)).as("B sees A's " + table).isZero();
        }
    }

    @Test
    void withoutATenantContextNothingIsVisible() {
        for (String table : TABLES) {
            assertThat(jdbc.sql("SELECT COUNT(*) FROM " + table).query(Long.class).single()).as(table).isZero();
        }
    }

    @Test
    void tenantBCannotInsertASurveyForTenantA() {
        assertThatThrownBy(() -> tenantScope.run(b.tenant(), () -> jdbc.sql("""
                        INSERT INTO survey (tenant_id, id, title, status, draft_definition, draft_version, created_by)
                        VALUES (:tenant, :id, 't', 'draft', '{}'::jsonb, 1, 'x')
                        """)
                .param("tenant", a.tenant().value())
                .param("id", UUID.randomUUID())
                .update()))
                .rootCause().hasMessageContaining("row-level security");
    }

    @Test
    void tenantBCannotInsertAPublishAttemptForTenantA() {
        assertThatThrownBy(() -> tenantScope.run(b.tenant(), () -> jdbc.sql("""
                        INSERT INTO survey_publish_attempt (tenant_id, request_id, survey_id, draft_version,
                            engine_instance_id, definition, outcome, requested_by)
                        VALUES (:tenant, :request, :survey, 1, 'eng-x', '{}'::jsonb, 'in_flight', 'x')
                        """)
                .param("tenant", a.tenant().value())
                .param("request", UUID.randomUUID())
                .param("survey", surveyOfA)
                .update()))
                .rootCause().hasMessageContaining("row-level security");
    }

    @Test
    void tenantBCannotInsertAPublishedVersionOrBindingForTenantA() {
        assertThatThrownBy(() -> tenantScope.run(b.tenant(), () -> jdbc.sql("""
                        INSERT INTO survey_published_version (tenant_id, survey_id, version_no, request_id,
                            draft_version, engine_instance_id, engine_sid, definition, compiler_version,
                            fingerprint_version, fingerprint, language, engine_published_at, binding, published_by)
                        VALUES (:tenant, :survey, 99, :request, 1, 'eng-x', 1, '{}'::jsonb, 'c', 'fm1', 'f', 'en',
                            'now', '{}'::jsonb, 'x')
                        """)
                .param("tenant", a.tenant().value())
                .param("survey", surveyOfA)
                .param("request", UUID.randomUUID())
                .update()))
                .rootCause().hasMessageContaining("row-level security");
        assertThatThrownBy(() -> tenantScope.run(b.tenant(), () -> jdbc.sql("""
                        INSERT INTO survey_question_binding (tenant_id, survey_id, version_no, ordinal,
                            question_uuid, question_code, question_type, fieldname, aid, scale)
                        VALUES (:tenant, :survey, 1, 99, :q, 'Q', 'L', 'Q999', '', 0)
                        """)
                .param("tenant", a.tenant().value())
                .param("survey", surveyOfA)
                .param("q", UUID.randomUUID())
                .update()))
                .rootCause().hasMessageContaining("row-level security");
    }

    @Test
    void tenantBUpdatesOfTenantAsSurveyAndAttemptsTouchNothing() {
        int surveys = tenantScope.call(b.tenant(), () -> jdbc
                .sql("UPDATE survey SET title = 'hijacked' WHERE id = :id").param("id", surveyOfA).update());
        int attempts = tenantScope.call(b.tenant(), () -> jdbc
                .sql("UPDATE survey_publish_attempt SET outcome = 'failed' WHERE survey_id = :id")
                .param("id", surveyOfA).update());

        assertThat(surveys).isZero();
        assertThat(attempts).isZero();
        String title = tenantScope.call(a.tenant(), () -> jdbc
                .sql("SELECT title FROM survey WHERE id = :id").param("id", surveyOfA).query(String.class).single());
        assertThat(title).isNotEqualTo("hijacked");
    }

    @Test
    void publishedVersionsAndBindingsCannotBeChangedOrDeletedEvenByTheirOwnTenant() {
        for (String table : new String[] {"survey_published_version", "survey_question_binding"}) {
            assertThatThrownBy(() -> tenantScope.run(a.tenant(), () -> jdbc
                    .sql("UPDATE " + table + " SET version_no = version_no WHERE survey_id = :id")
                    .param("id", surveyOfA).update()))
                    .as(table).rootCause().hasMessageContaining("permission denied");
            assertThatThrownBy(() -> tenantScope.run(a.tenant(), () -> jdbc
                    .sql("DELETE FROM " + table + " WHERE survey_id = :id")
                    .param("id", surveyOfA).update()))
                    .as(table).rootCause().hasMessageContaining("permission denied");
        }
    }

    @Test
    void surveysAndAttemptsCannotBeDeletedByTheRuntimeAccount() {
        for (String table : new String[] {"survey", "survey_publish_attempt"}) {
            String column = table.equals("survey") ? "id" : "survey_id";
            assertThatThrownBy(() -> tenantScope.run(a.tenant(), () -> jdbc
                    .sql("DELETE FROM " + table + " WHERE " + column + " = :id")
                    .param("id", surveyOfA).update()))
                    .as(table).rootCause().hasMessageContaining("permission denied");
        }
    }
}
