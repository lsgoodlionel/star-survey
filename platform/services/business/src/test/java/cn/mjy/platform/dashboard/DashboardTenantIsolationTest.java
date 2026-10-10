package cn.mjy.platform.dashboard;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.Mockito.clearInvocations;
import static org.mockito.Mockito.spy;
import static org.mockito.Mockito.times;
import static org.mockito.Mockito.verify;

import cn.mjy.platform.shared.TenantContext;
import cn.mjy.platform.shared.tenant.TenantScope;
import cn.mjy.platform.survey.SurveyFixture;
import cn.mjy.platform.survey.SurveyFixture.Workspace;
import cn.mjy.platform.survey.SurveyView;
import java.sql.Timestamp;
import java.time.Instant;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.List;
import java.util.UUID;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.jdbc.core.simple.JdbcClient;

@SpringBootTest
class DashboardTenantIsolationTest {

    @Autowired
    private DashboardService dashboard;

    @Autowired
    private SurveyFixture fixture;

    @Autowired
    private TenantScope tenantScope;

    @Autowired
    private JdbcClient jdbc;

    private Workspace tenantA;
    private Workspace tenantB;

    @BeforeEach
    void setUp() {
        tenantA = fixture.workspace();
        tenantB = fixture.workspace();
    }

    @Test
    void aggregateNeverLeaksAnotherTenantThroughRowsOrCounts() {
        SurveyView visible = fixture.newSurvey(tenantA);
        SurveyView hidden = fixture.newSurvey(tenantB);
        markPublishFailed(tenantA, visible.id(), Instant.parse("2026-10-10T10:00:00Z"));
        markPublishFailed(tenantB, hidden.id(), Instant.parse("2026-10-10T11:00:00Z"));

        DashboardView view = dashboard.getDashboard(tenantA.owner(), 50, 50);

        assertThat(view.summary().publishExceptions()).isOne();
        assertThat(view.surveys()).extracting(DashboardSurveyView::surveyId).containsExactly(visible.id());
        assertThat(view.tasks()).extracting(DashboardTaskView::surveyId).containsOnly(visible.id());
    }

    @Test
    void countsAndSectionsAreFilteredByEffectiveResourcePermissionsFirst() {
        SurveyView permitted = fixture.newSurvey(tenantA);
        SurveyView denied = fixture.newSurvey(tenantA);
        TenantContext reviewer = fixture.member(tenantA, "reviewer", "publish_reviewer", permitted.id());
        insertPendingApproval(tenantA, permitted.id(), "applicant-a", Instant.parse("2026-10-10T09:00:00Z"));
        insertPendingApproval(tenantA, denied.id(), "applicant-b", Instant.parse("2026-10-10T10:00:00Z"));

        DashboardView view = dashboard.getDashboard(reviewer, 50, 50);

        assertThat(view.visibleSections()).containsExactly("approval", "survey");
        assertThat(view.summary().pendingApprovals()).isOne();
        assertThat(view.summary().publishExceptions()).isZero();
        assertThat(view.summary().activePreviews()).isZero();
        assertThat(view.summary().activeExports()).isZero();
        assertThat(view.tasks()).extracting(DashboardTaskView::surveyId).containsExactly(permitted.id());
        assertThat(view.surveys()).extracting(DashboardSurveyView::surveyId).containsExactly(permitted.id());
        assertThat(view.surveys().getFirst().actions()).isEmpty();
        assertThat(view.surveys().getFirst().completedResponses()).isNull();
    }

    @Test
    void surveysAndTasksKeepStableTieBreakOrdering() {
        List<SurveyView> surveys = List.of(
                fixture.newSurvey(tenantA),
                fixture.newSurvey(tenantA),
                fixture.newSurvey(tenantA));
        Instant tied = Instant.parse("2026-10-10T12:00:00Z");
        surveys.forEach(survey -> markPublishFailed(tenantA, survey.id(), tied));
        List<UUID> expectedIds = surveys.stream().map(SurveyView::id)
                .sorted(Comparator.comparing(UUID::toString)).toList();

        DashboardView view = dashboard.getDashboard(tenantA.owner(), 50, 50);

        assertThat(view.surveys()).extracting(DashboardSurveyView::surveyId).containsExactlyElementsOf(expectedIds);
        assertThat(view.tasks()).extracting(DashboardTaskView::kind).containsOnly("publish_exception");
        assertThat(view.tasks()).extracting(DashboardTaskView::surveyId).containsExactlyElementsOf(expectedIds);
        List<String> taskKeys = view.tasks().stream().map(DashboardTaskView::taskKey).toList();
        assertThat(taskKeys).isSortedAccordingTo(Comparator.naturalOrder());
    }

    @Test
    void surveyAndTaskLimitsStayBoundedFromOneThroughFifty() {
        List<SurveyView> surveys = new ArrayList<>();
        for (int index = 0; index < 4; index++) {
            SurveyView survey = fixture.newSurvey(tenantA);
            markPublishFailed(tenantA, survey.id(), Instant.parse("2026-10-10T12:00:00Z").plusSeconds(index));
            surveys.add(survey);
        }

        DashboardView one = dashboard.getDashboard(tenantA.owner(), 1, 1);
        DashboardView all = dashboard.getDashboard(tenantA.owner(), 50, 50);

        assertThat(one.surveys()).hasSize(1);
        assertThat(one.tasks()).hasSize(1);
        assertThat(all.surveys()).hasSize(4);
        assertThat(all.tasks()).hasSize(4);
    }

    @Test
    void aggregateUsesOneSetBasedProjectionRegardlessOfSurveyCount() {
        JdbcClient observedJdbc = spy(jdbc);
        DashboardRepository observed = new DashboardRepository(observedJdbc);

        tenantScope.call(tenantA.tenant(), () -> observed.load(tenantA.tenant(), tenantA.owner().actorId(), 50, 50));
        verify(observedJdbc, times(1)).sql(anyString());
        for (int index = 0; index < 12; index++) {
            fixture.newSurvey(tenantA);
        }
        clearInvocations(observedJdbc);
        tenantScope.call(tenantA.tenant(), () -> observed.load(tenantA.tenant(), tenantA.owner().actorId(), 50, 50));

        verify(observedJdbc, times(1)).sql(anyString());
    }

    private void markPublishFailed(Workspace workspace, UUID surveyId, Instant updatedAt) {
        tenantScope.run(workspace.tenant(), () -> jdbc.sql("""
                        UPDATE survey
                        SET status = 'publish_failed', current_request_id = :request, updated_at = :updated
                        WHERE id = :survey
                        """)
                .param("request", UUID.randomUUID())
                .param("updated", Timestamp.from(updatedAt))
                .param("survey", surveyId)
                .update());
    }

    private void insertPendingApproval(Workspace workspace, UUID surveyId, String applicant, Instant submittedAt) {
        tenantScope.run(workspace.tenant(), () -> jdbc.sql("""
                        INSERT INTO survey_publish_approval
                            (tenant_id, id, survey_id, draft_version, status, applicant, submitted_at)
                        VALUES (:tenant, :id, :survey, 1, 'pending', :applicant, :submitted)
                        """)
                .param("tenant", workspace.tenant().value())
                .param("id", UUID.randomUUID())
                .param("survey", surveyId)
                .param("applicant", applicant)
                .param("submitted", Timestamp.from(submittedAt))
                .update());
    }
}
