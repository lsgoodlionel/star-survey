package cn.mjy.platform.survey;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import cn.mjy.platform.shared.TenantContext;
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
 * 审批两张表由行级安全隔离，且决定历史只追加。故意绕过服务层直接写 SQL，证明挡住的是数据库。
 */
@SpringBootTest
class PublishApprovalTenantIsolationTest {

    private static final String[] TABLES = {"survey_publish_approval", "survey_publish_approval_event"};

    @Autowired
    private SurveyFixture fixture;

    @Autowired
    private PublishApprovalService approvals;

    @Autowired
    private TenantScope tenantScope;

    @Autowired
    private JdbcClient jdbc;

    private Workspace a;
    private Workspace b;
    private UUID surveyOfA;
    private UUID requestOfA;

    @BeforeEach
    void tenantAHasAnApprovedRequest() {
        a = fixture.workspace();
        b = fixture.workspace();
        surveyOfA = fixture.newSurvey(a).id();
        TenantContext editor = fixture.member(a, "sub-editor", "editor", surveyOfA);
        requestOfA = approvals.submit(editor, surveyOfA, 1).id();
        approvals.approve(a.owner(), requestOfA);
    }

    private long rowsOfSurveyA(TenantId scope, String table) {
        return tenantScope.call(scope, () -> jdbc
                .sql("SELECT COUNT(*) FROM " + table + " WHERE survey_id = :id")
                .param("id", surveyOfA)
                .query(Long.class).single());
    }

    @Test
    void tenantBSeesNoneOfTenantAsApprovalRows() {
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
    void tenantBCannotInsertApprovalsOrEventsForTenantA() {
        assertThatThrownBy(() -> tenantScope.run(b.tenant(), () -> jdbc.sql("""
                        INSERT INTO survey_publish_approval (tenant_id, id, survey_id, draft_version, status, applicant)
                        VALUES (:tenant, :id, :survey, 1, 'pending', 'mallory')
                        """)
                .param("tenant", a.tenant().value())
                .param("id", UUID.randomUUID())
                .param("survey", surveyOfA)
                .update()))
                .rootCause().hasMessageContaining("row-level security");
        assertThatThrownBy(() -> tenantScope.run(b.tenant(), () -> jdbc.sql("""
                        INSERT INTO survey_publish_approval_event (tenant_id, approval_id, survey_id, event, actor,
                            draft_version)
                        VALUES (:tenant, :approval, :survey, 'approved', 'mallory', 1)
                        """)
                .param("tenant", a.tenant().value())
                .param("approval", requestOfA)
                .param("survey", surveyOfA)
                .update()))
                .rootCause().hasMessageContaining("row-level security");
    }

    @Test
    void tenantBUpdatesOfTenantAsApprovalTouchNothing() {
        int updated = tenantScope.call(b.tenant(), () -> jdbc
                .sql("UPDATE survey_publish_approval SET status = 'rejected' WHERE id = :id")
                .param("id", requestOfA).update());

        assertThat(updated).isZero();
        String status = tenantScope.call(a.tenant(), () -> jdbc
                .sql("SELECT status FROM survey_publish_approval WHERE id = :id")
                .param("id", requestOfA).query(String.class).single());
        assertThat(status).isEqualTo("approved");
    }

    @Test
    void theDecisionHistoryCannotBeChangedOrDeletedEvenByItsOwnTenant() {
        assertThatThrownBy(() -> tenantScope.run(a.tenant(), () -> jdbc
                .sql("UPDATE survey_publish_approval_event SET actor = 'someone-else' WHERE survey_id = :id")
                .param("id", surveyOfA).update()))
                .rootCause().hasMessageContaining("permission denied");
        assertThatThrownBy(() -> tenantScope.run(a.tenant(), () -> jdbc
                .sql("DELETE FROM survey_publish_approval_event WHERE survey_id = :id")
                .param("id", surveyOfA).update()))
                .rootCause().hasMessageContaining("permission denied");
    }

    @Test
    void approvalRequestsCannotBeDeletedByTheRuntimeAccount() {
        assertThatThrownBy(() -> tenantScope.run(a.tenant(), () -> jdbc
                .sql("DELETE FROM survey_publish_approval WHERE id = :id")
                .param("id", requestOfA).update()))
                .rootCause().hasMessageContaining("permission denied");
    }
}
