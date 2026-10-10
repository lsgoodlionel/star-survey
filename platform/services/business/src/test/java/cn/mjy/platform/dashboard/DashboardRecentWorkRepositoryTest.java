package cn.mjy.platform.dashboard;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import cn.mjy.platform.shared.TenantId;
import cn.mjy.platform.shared.tenant.TenantScope;
import cn.mjy.platform.survey.SurveyFixture;
import cn.mjy.platform.survey.SurveyFixture.Workspace;
import cn.mjy.platform.survey.SurveyView;
import java.time.Instant;
import java.util.Comparator;
import java.util.List;
import java.util.Map;
import java.util.UUID;
import java.util.function.Function;
import java.util.stream.Collectors;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.jdbc.core.simple.JdbcClient;

@SpringBootTest
class DashboardRecentWorkRepositoryTest {

    @Autowired
    private DashboardRecentWorkRepository recentWork;

    @Autowired
    private SurveyFixture surveys;

    @Autowired
    private TenantScope tenantScope;

    @Autowired
    private JdbcClient jdbc;

    private Workspace workspace;
    private SurveyView survey;

    @BeforeEach
    void setUp() {
        workspace = surveys.workspace();
        survey = surveys.newSurvey(workspace);
    }

    @Test
    void controlledPagesRequireTheOnlyValidVersionCombinationAndGenerateServerPaths() {
        UUID surveyId = survey.id();
        Instant visitedAt = Instant.parse("2026-10-10T08:00:00Z");
        Map<DashboardPage, String> expectedPaths = Map.of(
                DashboardPage.EDIT, "/surveys/" + surveyId + "/edit",
                DashboardPage.IMPORT, "/surveys/" + surveyId + "/import",
                DashboardPage.PREVIEW, "/surveys/" + surveyId + "/preview",
                DashboardPage.PUBLISH, "/surveys/" + surveyId + "/publish",
                DashboardPage.RESPONSES, "/surveys/" + surveyId + "/responses",
                DashboardPage.VERSION, "/surveys/" + surveyId + "/versions/3");

        for (DashboardPage page : DashboardPage.values()) {
            Integer version = page == DashboardPage.VERSION ? 3 : null;
            assertThat(recentWork.upsert(workspace.tenant(), workspace.owner().actorId(),
                    new RecentWorkCommand(surveyId, page, version), visitedAt)).isTrue();
        }

        Map<DashboardPage, RecentWorkView> views = recentWork
                .findVisible(workspace.tenant(), workspace.owner().actorId(), 50).stream()
                .collect(Collectors.toMap(RecentWorkView::page, Function.identity()));
        assertThat(views).hasSize(DashboardPage.values().length);
        assertThat(views).allSatisfy((page, view) -> {
            assertThat(view.surveyId()).isEqualTo(surveyId);
            assertThat(view.surveyName()).isEqualTo(survey.title());
            assertThat(view.targetPath()).isEqualTo(expectedPaths.get(page));
            assertThat(view.visitedAt()).isEqualTo(visitedAt);
        });

        assertThatThrownBy(() -> new RecentWorkCommand(surveyId, DashboardPage.VERSION, null))
                .isInstanceOf(IllegalArgumentException.class);
        assertThatThrownBy(() -> new RecentWorkCommand(surveyId, DashboardPage.VERSION, 0))
                .isInstanceOf(IllegalArgumentException.class);
        assertThatThrownBy(() -> new RecentWorkCommand(surveyId, DashboardPage.EDIT, 1))
                .isInstanceOf(IllegalArgumentException.class);
    }

    @Test
    void upsertingTheSameTargetOnlyMovesItsTimestamp() {
        Instant first = Instant.parse("2026-10-10T08:00:00Z");
        Instant second = Instant.parse("2026-10-10T09:00:00Z");
        RecentWorkCommand target = new RecentWorkCommand(survey.id(), DashboardPage.EDIT, null);

        recentWork.upsert(workspace.tenant(), workspace.owner().actorId(), target, first);
        recentWork.upsert(workspace.tenant(), workspace.owner().actorId(), target, second);

        assertThat(recentWork.findVisible(workspace.tenant(), workspace.owner().actorId(), 50))
                .singleElement().extracting(RecentWorkView::visitedAt).isEqualTo(second);
        assertThat(rowCount(workspace.tenant(), workspace.owner().actorId())).isOne();
    }

    @Test
    void equalTimestampsHaveADeterministicTargetOrder() {
        SurveyView secondSurvey = surveys.newSurvey(workspace);
        Instant sameTime = Instant.parse("2026-10-10T08:00:00Z");
        List<RecentWorkCommand> commands = List.of(
                new RecentWorkCommand(secondSurvey.id(), DashboardPage.PREVIEW, null),
                new RecentWorkCommand(survey.id(), DashboardPage.VERSION, 2),
                new RecentWorkCommand(survey.id(), DashboardPage.EDIT, null));
        commands.forEach(command -> recentWork.upsert(
                workspace.tenant(), workspace.owner().actorId(), command, sameTime));

        List<String> actual = recentWork.findVisible(workspace.tenant(), workspace.owner().actorId(), 50).stream()
                .map(view -> view.surveyId() + ":" + view.page().code() + ":" + view.targetPath())
                .toList();
        List<String> expected = commands.stream()
                .sorted(Comparator.comparing((RecentWorkCommand command) -> command.surveyId().toString())
                        .thenComparing(command -> command.page().code())
                        .thenComparing(command -> command.version() == null ? 0 : command.version()))
                .map(command -> command.surveyId() + ":" + command.page().code() + ":"
                        + command.page().targetPath(command.surveyId(), command.version()))
                .toList();

        assertThat(actual).containsExactlyElementsOf(expected);
    }

    @Test
    void theFiftyFirstDistinctTargetEvictsTheOldestOne() {
        Instant start = Instant.parse("2026-10-10T08:00:00Z");
        for (int version = 1; version <= 51; version++) {
            recentWork.upsert(workspace.tenant(), workspace.owner().actorId(),
                    new RecentWorkCommand(survey.id(), DashboardPage.VERSION, version),
                    start.plusSeconds(version));
        }

        List<RecentWorkView> views = recentWork.findVisible(workspace.tenant(), workspace.owner().actorId(), 50);
        assertThat(views).hasSize(50);
        assertThat(views).extracting(RecentWorkView::targetPath)
                .containsExactlyElementsOf(java.util.stream.IntStream.rangeClosed(2, 51)
                        .boxed().sorted(Comparator.reverseOrder())
                        .map(version -> "/surveys/" + survey.id() + "/versions/" + version)
                        .toList());
        assertThat(rowCount(workspace.tenant(), workspace.owner().actorId())).isEqualTo(50);
    }

    @Test
    void findVisibleRejectsLimitsOutsideTheRepositoryBoundary() {
        assertThatThrownBy(() -> recentWork.findVisible(workspace.tenant(), workspace.owner().actorId(), 0))
                .isInstanceOf(IllegalArgumentException.class);
        assertThatThrownBy(() -> recentWork.findVisible(workspace.tenant(), workspace.owner().actorId(), 51))
                .isInstanceOf(IllegalArgumentException.class);
    }

    private long rowCount(TenantId tenant, String actorId) {
        return tenantScope.call(tenant, () -> jdbc.sql("""
                        SELECT count(*) FROM dashboard_recent_work
                        WHERE tenant_id = :tenant AND actor_id = :actor
                        """)
                .param("tenant", tenant.value())
                .param("actor", actorId)
                .query(Long.class)
                .single());
    }
}
