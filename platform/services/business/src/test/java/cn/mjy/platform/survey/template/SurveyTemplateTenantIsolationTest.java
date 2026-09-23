package cn.mjy.platform.survey.template;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import cn.mjy.platform.shared.TenantContext;
import cn.mjy.platform.shared.TenantId;
import cn.mjy.platform.shared.tenant.TenantScope;
import cn.mjy.platform.survey.SurveyFixture;
import cn.mjy.platform.survey.SurveyFixture.Workspace;
import java.util.UUID;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.ValueSource;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.jdbc.core.simple.JdbcClient;

/**
 * 模板库三张租户表由行级安全隔离，版本快照与动作历史只追加。故意绕过服务层直接写 SQL，证明挡住的是数据库。
 */
@SpringBootTest
class SurveyTemplateTenantIsolationTest {

    private static final String[] TABLES = {"survey_template", "survey_template_version", "survey_template_event"};

    @Autowired
    private SurveyFixture fixture;

    @Autowired
    private SurveyTemplateService templates;

    @Autowired
    private TenantScope tenantScope;

    @Autowired
    private JdbcClient jdbc;

    private Workspace a;
    private Workspace b;
    private UUID templateOfA;

    @BeforeEach
    void tenantAHasAPublishedTemplate() {
        a = fixture.workspace();
        b = fixture.workspace();
        TenantContext reviewer = fixture.member(a, "sub-reviewer", "publish_reviewer", null);
        UUID surveyId = fixture.newSurvey(a).id();
        templateOfA = templates.createFromSurvey(a.owner(), surveyId, "标准满意度", null).id();
        templates.submitForReview(a.owner(), templateOfA);
        templates.approve(reviewer, templateOfA);
    }

    private long rowsOfTenantA(TenantId scope, String table) {
        String column = "survey_template".equals(table) ? "id" : "template_id";
        return tenantScope.call(scope, () -> jdbc
                .sql("SELECT COUNT(*) FROM " + table + " WHERE " + column + " = :id")
                .param("id", templateOfA)
                .query(Long.class).single());
    }

    @ParameterizedTest
    @ValueSource(strings = {"survey_template", "survey_template_version", "survey_template_event"})
    void tenantBSeesNoneOfTenantAsTemplateRows(String table) {
        assertThat(rowsOfTenantA(a.tenant(), table)).as("A sees own " + table).isPositive();
        assertThat(rowsOfTenantA(b.tenant(), table)).as("B sees A's " + table).isZero();
    }

    @Test
    void withoutATenantContextNothingIsVisible() {
        for (String table : TABLES) {
            assertThat(jdbc.sql("SELECT COUNT(*) FROM " + table).query(Long.class).single()).as(table).isZero();
        }
    }

    @Test
    void tenantBCannotInsertTemplateRowsForTenantA() {
        assertThatThrownBy(() -> tenantScope.run(b.tenant(), () -> jdbc.sql("""
                        INSERT INTO survey_template (tenant_id, id, name, status, current_version, created_by)
                        VALUES (:tenant, :id, 'mallory', 'draft', 1, 'mallory')
                        """)
                .param("tenant", a.tenant().value())
                .param("id", UUID.randomUUID())
                .update()))
                .rootCause().hasMessageContaining("row-level security");
        assertThatThrownBy(() -> tenantScope.run(b.tenant(), () -> jdbc.sql("""
                        INSERT INTO survey_template_version
                            (tenant_id, template_id, version_no, definition, created_by)
                        VALUES (:tenant, :template, 99, CAST('{}' AS jsonb), 'mallory')
                        """)
                .param("tenant", a.tenant().value())
                .param("template", templateOfA)
                .update()))
                .rootCause().hasMessageContaining("row-level security");
        assertThatThrownBy(() -> tenantScope.run(b.tenant(), () -> jdbc.sql("""
                        INSERT INTO survey_template_event (tenant_id, template_id, event, actor)
                        VALUES (:tenant, :template, 'approved', 'mallory')
                        """)
                .param("tenant", a.tenant().value())
                .param("template", templateOfA)
                .update()))
                .rootCause().hasMessageContaining("row-level security");
    }

    @Test
    void tenantBUpdatesOfTenantAsTemplateTouchNothing() {
        int updated = tenantScope.call(b.tenant(), () -> jdbc
                .sql("UPDATE survey_template SET status = 'unpublished', published_version = NULL WHERE id = :id")
                .param("id", templateOfA).update());

        assertThat(updated).isZero();
        String status = tenantScope.call(a.tenant(), () -> jdbc
                .sql("SELECT status FROM survey_template WHERE id = :id")
                .param("id", templateOfA).query(String.class).single());
        assertThat(status).isEqualTo("published");
    }

    @Test
    void tenantBCannotReadTenantAsTemplateDefinition() {
        assertThat(tenantScope.call(b.tenant(), () -> jdbc
                .sql("SELECT COUNT(*) FROM survey_template_version WHERE template_id = :id")
                .param("id", templateOfA).query(Long.class).single())).isZero();
    }

    @Test
    void snapshotsAndHistoryCannotBeChangedOrDeletedEvenByTheirOwnTenant() {
        assertThatThrownBy(() -> tenantScope.run(a.tenant(), () -> jdbc
                .sql("UPDATE survey_template_version SET definition = CAST('{}' AS jsonb) WHERE template_id = :id")
                .param("id", templateOfA).update()))
                .rootCause().hasMessageContaining("permission denied");
        assertThatThrownBy(() -> tenantScope.run(a.tenant(), () -> jdbc
                .sql("DELETE FROM survey_template_version WHERE template_id = :id")
                .param("id", templateOfA).update()))
                .rootCause().hasMessageContaining("permission denied");
        assertThatThrownBy(() -> tenantScope.run(a.tenant(), () -> jdbc
                .sql("UPDATE survey_template_event SET actor = 'someone-else' WHERE template_id = :id")
                .param("id", templateOfA).update()))
                .rootCause().hasMessageContaining("permission denied");
        assertThatThrownBy(() -> tenantScope.run(a.tenant(), () -> jdbc
                .sql("DELETE FROM survey_template_event WHERE template_id = :id")
                .param("id", templateOfA).update()))
                .rootCause().hasMessageContaining("permission denied");
    }

    @Test
    void templatesCannotBeDeletedByTheRuntimeAccount() {
        assertThatThrownBy(() -> tenantScope.run(a.tenant(), () -> jdbc
                .sql("DELETE FROM survey_template WHERE id = :id")
                .param("id", templateOfA).update()))
                .rootCause().hasMessageContaining("permission denied");
    }

    @Test
    void thePlatformTemplateTablesAreControlPlaneAndAppendOnlyForVersions() {
        // 运营模板刻意不做行级安全（一份运营模板要对所有租户可见），但运行期账号仍然不能删除它，
        // 版本快照也不能被改写。
        assertThatThrownBy(() -> jdbc.sql("DELETE FROM platform_survey_template").update())
                .rootCause().hasMessageContaining("permission denied");
        assertThatThrownBy(() -> jdbc.sql("DELETE FROM platform_survey_template_version").update())
                .rootCause().hasMessageContaining("permission denied");
        assertThatThrownBy(() -> jdbc
                .sql("UPDATE platform_survey_template_version SET created_by = 'mallory'").update())
                .rootCause().hasMessageContaining("permission denied");
    }
}
