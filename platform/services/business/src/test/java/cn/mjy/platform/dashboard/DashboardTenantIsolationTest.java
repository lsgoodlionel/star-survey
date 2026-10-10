package cn.mjy.platform.dashboard;

import static org.assertj.core.api.Assertions.assertThat;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

import cn.mjy.platform.access.AccessFixture;
import cn.mjy.platform.access.GrantRequest;
import cn.mjy.platform.shared.TenantContext;
import cn.mjy.platform.shared.tenant.TenantScope;
import cn.mjy.platform.support.TestTokens;
import cn.mjy.platform.survey.PublishedVersionView;
import cn.mjy.platform.survey.SurveyFixture;
import cn.mjy.platform.survey.SurveyFixture.Workspace;
import cn.mjy.platform.survey.SurveyPublishService;
import cn.mjy.platform.survey.SurveyView;
import java.lang.reflect.InvocationTargetException;
import java.lang.reflect.Proxy;
import java.sql.Timestamp;
import java.time.Instant;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.List;
import java.util.UUID;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.Arguments;
import org.junit.jupiter.params.provider.MethodSource;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.beans.factory.config.BeanPostProcessor;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.test.context.TestConfiguration;
import org.springframework.boot.webmvc.test.autoconfigure.AutoConfigureMockMvc;
import org.springframework.context.ApplicationContext;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Import;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.test.web.servlet.MockMvc;

@SpringBootTest
@AutoConfigureMockMvc
@Import(DashboardTenantIsolationTest.QueryCountingConfiguration.class)
class DashboardTenantIsolationTest {

    @Autowired
    private DashboardService dashboard;

    @Autowired
    private SurveyFixture fixture;

    @Autowired
    private TenantScope tenantScope;

    @Autowired
    private JdbcClient jdbc;

    @Autowired
    private DashboardQueryCounter queryCounter;

    @Autowired
    private ApplicationContext applicationContext;

    @Autowired
    private AccessFixture access;

    @Autowired
    private SurveyPublishService publisher;

    @Autowired
    private MockMvc mvc;

    @Autowired
    private TestTokens tokens;

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
    void tenantWidePermissionsRemainVisibleWithoutAnySurveyData() {
        TenantContext reviewer = access.activeMember(tenantA.owner(), "tenant-reviewer");
        access.grants().grant(tenantA.owner(),
                new GrantRequest(reviewer.actorId(), "publish_reviewer", null, null));

        DashboardView view = dashboard.getDashboard(reviewer, 50, 50);

        assertThat(view.visibleSections()).containsExactly("approval", "survey");
        assertThat(view.summary()).isEqualTo(new DashboardSummary(0, 0, 0, 0));
        assertThat(view.tasks()).isEmpty();
        assertThat(view.surveys()).isEmpty();
    }

    @Test
    void tenantWidePermissionsRemainVisibleWhenEverySurveyIsArchived() {
        SurveyView archived = fixture.newSurvey(tenantA);
        TenantContext editor = access.activeMember(tenantA.owner(), "tenant-editor");
        access.grants().grant(tenantA.owner(), new GrantRequest(editor.actorId(), "editor", null, null));
        archive(tenantA, archived.id());

        DashboardView view = dashboard.getDashboard(editor, 50, 50);

        assertThat(view.visibleSections()).containsExactly("publish", "preview", "survey");
        assertThat(view.summary()).isEqualTo(new DashboardSummary(0, 0, 0, 0));
        assertThat(view.tasks()).isEmpty();
        assertThat(view.surveys()).isEmpty();
    }

    @Test
    void resourceScopedPermissionsDisappearWhenTheirOnlySurveyIsArchived() {
        SurveyView archived = fixture.newSurvey(tenantA);
        TenantContext reviewer = fixture.member(tenantA, "scoped-reviewer", "publish_reviewer", archived.id());
        archive(tenantA, archived.id());

        DashboardView view = dashboard.getDashboard(reviewer, 50, 50);

        assertThat(view.visibleSections()).isEmpty();
        assertThat(view.summary()).isEqualTo(new DashboardSummary(0, 0, 0, 0));
    }

    @ParameterizedTest
    @MethodSource("runtimePublishStates")
    void runtimePublishStateTakesPriorityOverAnApprovedRequest(String databaseState, String expectedState) {
        SurveyView survey = fixture.newSurvey(tenantA);
        markRuntimeStateWithApprovedRequest(tenantA, survey.id(), databaseState);

        DashboardView view = dashboard.getDashboard(tenantA.owner(), 50, 50);

        assertThat(view.surveys()).filteredOn(item -> item.surveyId().equals(survey.id()))
                .singleElement().extracting(DashboardSurveyView::publishState).isEqualTo(expectedState);
    }

    static java.util.stream.Stream<Arguments> runtimePublishStates() {
        return java.util.stream.Stream.of(
                Arguments.of("publishing", "publishing"),
                Arguments.of("publish_failed", "failed"),
                Arguments.of("pending_reconciliation", "needs_reconciliation"));
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
    void dashboardHttpGetUsesAFixedQueryCountRegardlessOfSurveyCount() throws Exception {
        int emptyDatasetQueries = dashboardHttpQueryCount();

        for (int index = 0; index < 12; index++) {
            fixture.newSurvey(tenantA);
        }
        int populatedDatasetQueries = dashboardHttpQueryCount();

        assertThat(emptyDatasetQueries).isEqualTo(4);
        assertThat(populatedDatasetQueries).isEqualTo(emptyDatasetQueries);
    }

    @Test
    void queryCounterWrapsBootAutoConfiguredJdbcClient() {
        assertThat(applicationContext.getBeanNamesForType(JdbcClient.class)).containsExactly("jdbcClient");
        assertThat(applicationContext.getBean("jdbcClient")).isSameAs(jdbc);
    }

    @Test
    void dashboardQueryCounterIgnoresConcurrentSqlFromAnotherThread() throws Exception {
        queryCounter.start();
        Thread background = Thread.ofPlatform().start(
                () -> jdbc.sql("SELECT 1").query(Integer.class).single());
        background.join();
        jdbc.sql("SELECT 1").query(Integer.class).single();

        assertThat(queryCounter.stop()).isOne();
    }

    @Test
    void dashboardHttpGetExcludesCrossTenantPreviewExportAndResponseCounts() throws Exception {
        SurveyView surveyA = fixture.newSurvey(tenantA);
        SurveyView surveyB = fixture.newSurvey(tenantB);
        PublishedVersionView publishedA = publisher.publish(tenantA.owner(), surveyA.id()).version();
        PublishedVersionView publishedB = publisher.publish(tenantB.owner(), surveyB.id()).version();
        insertOperationalCounts(tenantA, surveyA.id(), publishedA, 1);
        insertOperationalCounts(tenantB, surveyB.id(), publishedB, 2);

        mvc.perform(get("/v1/dashboard?surveyLimit=50&taskLimit=50")
                        .header("Authorization", bearer(tenantA.owner())))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.summary.activePreviews").value(1))
                .andExpect(jsonPath("$.summary.activeExports").value(1))
                .andExpect(jsonPath("$.surveys[?(@.surveyId == '%s')].completedResponses"
                        .formatted(surveyA.id())).value(1))
                .andExpect(jsonPath("$.surveys[?(@.surveyId == '%s')]"
                        .formatted(surveyB.id())).isEmpty());
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

    private void archive(Workspace workspace, UUID surveyId) {
        tenantScope.run(workspace.tenant(), () -> jdbc.sql("""
                        UPDATE access_resource SET archived_at = now()
                        WHERE tenant_id = :tenant AND id = :survey
                        """)
                .param("tenant", workspace.tenant().value())
                .param("survey", surveyId)
                .update());
    }

    private void markRuntimeStateWithApprovedRequest(Workspace workspace, UUID surveyId, String status) {
        tenantScope.run(workspace.tenant(), () -> {
            UUID requestId = UUID.randomUUID();
            jdbc.sql("""
                            UPDATE survey
                            SET status = :status, current_request_id = :request,
                                publishing_started_at = CASE WHEN :status = 'publishing' THEN now() ELSE NULL END
                            WHERE id = :survey
                            """)
                    .param("status", status)
                    .param("request", requestId)
                    .param("survey", surveyId)
                    .update();
            jdbc.sql("""
                            INSERT INTO survey_publish_approval
                                (tenant_id, id, survey_id, draft_version, status, applicant,
                                 submitted_at, decided_by, decided_at)
                            VALUES (:tenant, :id, :survey, 1, 'approved', 'applicant',
                                    now(), 'reviewer', now())
                            """)
                    .param("tenant", workspace.tenant().value())
                    .param("id", UUID.randomUUID())
                    .param("survey", surveyId)
                    .update();
        });
    }

    private void insertOperationalCounts(Workspace workspace, UUID surveyId,
            PublishedVersionView published, int count) {
        tenantScope.run(workspace.tenant(), () -> {
            for (int index = 0; index < count; index++) {
                jdbc.sql("""
                                INSERT INTO response_projection
                                    (tenant_id, engine_instance_id, survey_id, generation, response_id,
                                     state, first_event_at, completed_at, last_event_id)
                                VALUES (:tenant, :instance, :sid, :generation, :response,
                                        'engine_completed', now(), now(), :event)
                                """)
                        .param("tenant", workspace.tenant().value())
                        .param("instance", published.engineInstanceId())
                        .param("sid", published.engineSid())
                        .param("generation", "dashboard-" + index)
                        .param("response", index + 1L)
                        .param("event", UUID.randomUUID())
                        .update();
            }
            jdbc.sql("""
                            INSERT INTO survey_preview_session
                                (tenant_id, id, request_id, survey_id, draft_version, definition,
                                 requested_by, engine_instance_id, engine_sid, generation,
                                 expires_at, status)
                            VALUES (:tenant, :id, :request, :survey, 1, '{}'::jsonb,
                                    :actor, :instance, :sid, :generation,
                                    now() + interval '1 hour', 'ready')
                            """)
                    .param("tenant", workspace.tenant().value())
                    .param("id", UUID.randomUUID())
                    .param("request", UUID.randomUUID())
                    .param("survey", surveyId)
                    .param("actor", workspace.owner().actorId())
                    .param("instance", workspace.engineInstanceId())
                    .param("sid", 990_000 + count)
                    .param("generation", "preview-dashboard-" + count)
                    .update();
            jdbc.sql("""
                            INSERT INTO response_export_job
                                (tenant_id, id, survey_id, requested_by, format,
                                 filter_snapshot, plan, reveal_sensitive, batch_size,
                                 status, expires_at)
                            VALUES (:tenant, :id, :survey, :actor, 'csv',
                                    '{}'::jsonb, '{}'::jsonb, false, 100,
                                    'queued', now() + interval '1 hour')
                            """)
                    .param("tenant", workspace.tenant().value())
                    .param("id", UUID.randomUUID())
                    .param("survey", surveyId)
                    .param("actor", workspace.owner().actorId())
                    .update();
        });
    }

    private String bearer(TenantContext context) {
        return "Bearer " + tokens.issue(context.actorId(), context.tenantId().value(), List.of());
    }

    private int dashboardHttpQueryCount() throws Exception {
        queryCounter.start();
        int queries;
        try {
            mvc.perform(get("/v1/dashboard?surveyLimit=50&taskLimit=50")
                            .header("Authorization", bearer(tenantA.owner())))
                    .andExpect(status().isOk());
        } finally {
            queries = queryCounter.stop();
        }
        return queries;
    }

    static final class DashboardQueryCounter implements BeanPostProcessor {
        private final ThreadLocal<Integer> requestThreadCount = new ThreadLocal<>();

        void start() {
            requestThreadCount.set(0);
        }

        void record() {
            Integer count = requestThreadCount.get();
            if (count != null) {
                requestThreadCount.set(count + 1);
            }
        }

        int stop() {
            Integer count = requestThreadCount.get();
            requestThreadCount.remove();
            if (count == null) {
                throw new IllegalStateException("query counter is not active on this thread");
            }
            return count;
        }

        @Override
        public Object postProcessAfterInitialization(Object bean, String beanName) {
            if (!beanName.equals("jdbcClient")) {
                return bean;
            }
            JdbcClient delegate = (JdbcClient) bean;
            return Proxy.newProxyInstance(
                    JdbcClient.class.getClassLoader(),
                    new Class<?>[] { JdbcClient.class },
                    (proxy, method, arguments) -> {
                        if (method.getName().equals("sql")) {
                            record();
                        }
                        try {
                            return method.invoke(delegate, arguments);
                        } catch (InvocationTargetException exception) {
                            throw exception.getCause();
                        }
                    });
        }
    }

    @TestConfiguration(proxyBeanMethods = false)
    static class QueryCountingConfiguration {
        @Bean
        static DashboardQueryCounter dashboardQueryCounter() {
            return new DashboardQueryCounter();
        }
    }
}
