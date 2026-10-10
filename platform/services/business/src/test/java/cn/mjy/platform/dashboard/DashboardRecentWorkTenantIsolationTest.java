package cn.mjy.platform.dashboard;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import cn.mjy.platform.access.AccessFixture;
import cn.mjy.platform.access.GrantRequest;
import cn.mjy.platform.access.MemberService;
import cn.mjy.platform.access.ResourceTreeService;
import cn.mjy.platform.shared.TenantContext;
import cn.mjy.platform.shared.TenantId;
import cn.mjy.platform.shared.tenant.TenantScope;
import cn.mjy.platform.survey.SurveyFixture;
import cn.mjy.platform.survey.SurveyFixture.Workspace;
import cn.mjy.platform.survey.SurveyView;
import java.sql.Timestamp;
import java.sql.Types;
import java.time.Instant;
import java.util.UUID;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.jdbc.core.simple.JdbcClient;

@SpringBootTest
class DashboardRecentWorkTenantIsolationTest {

    @Autowired
    private DashboardRecentWorkRepository recentWork;

    @Autowired
    private SurveyFixture surveys;

    @Autowired
    private ResourceTreeService resourceTree;

    @Autowired
    private MemberService members;

    @Autowired
    private AccessFixture access;

    @Autowired
    private TenantScope tenantScope;

    @Autowired
    private JdbcClient jdbc;

    private Workspace tenantA;
    private Workspace tenantB;
    private SurveyView surveyA;
    private SurveyView surveyB;
    private Instant now;

    @BeforeEach
    void setUp() {
        tenantA = surveys.workspace();
        tenantB = surveys.workspace();
        surveyA = surveys.newSurvey(tenantA);
        surveyB = surveys.newSurvey(tenantB);
        now = Instant.parse("2026-10-10T08:00:00Z");
    }

    @Test
    void tenantAndActorScopesCannotSeeEachOthersRows() {
        TenantContext editor = surveys.member(tenantA, "editor-a", "editor", surveyA.id());
        recentWork.upsert(tenantA.tenant(), tenantA.owner().actorId(),
                new RecentWorkCommand(surveyA.id(), DashboardPage.EDIT, null), now);
        recentWork.upsert(tenantA.tenant(), editor.actorId(),
                new RecentWorkCommand(surveyA.id(), DashboardPage.PREVIEW, null), now.plusSeconds(1));
        recentWork.upsert(tenantB.tenant(), tenantB.owner().actorId(),
                new RecentWorkCommand(surveyB.id(), DashboardPage.PUBLISH, null), now.plusSeconds(2));

        assertThat(recentWork.findVisible(tenantA.tenant(), tenantA.owner().actorId(), 50))
                .extracting(RecentWorkView::page).containsExactly(DashboardPage.EDIT);
        assertThat(recentWork.findVisible(tenantA.tenant(), editor.actorId(), 50))
                .extracting(RecentWorkView::page).containsExactly(DashboardPage.PREVIEW);
        assertThat(recentWork.findVisible(tenantB.tenant(), tenantB.owner().actorId(), 50))
                .extracting(RecentWorkView::surveyId).containsExactly(surveyB.id());

        long tenantARowsSeenByB = tenantScope.call(tenantB.tenant(), () -> jdbc.sql("""
                        SELECT count(*) FROM dashboard_recent_work WHERE tenant_id = :tenantA
                        """)
                .param("tenantA", tenantA.tenant().value())
                .query(Long.class)
                .single());
        assertThat(tenantARowsSeenByB).isZero();
    }

    @Test
    void anInvisibleOrCrossTenantSurveyCannotBeRecorded() {
        TenantContext unprivileged = AccessFixture.context(tenantA.tenant(), "unknown-actor");

        assertThat(recentWork.upsert(tenantA.tenant(), unprivileged.actorId(),
                new RecentWorkCommand(surveyA.id(), DashboardPage.EDIT, null), now)).isFalse();
        assertThat(recentWork.upsert(tenantB.tenant(), tenantB.owner().actorId(),
                new RecentWorkCommand(surveyA.id(), DashboardPage.EDIT, null), now)).isFalse();
        assertThat(recentWork.findVisible(tenantA.tenant(), unprivileged.actorId(), 50)).isEmpty();
    }

    @Test
    void losingAccessFiltersStoredWorkWithoutDeletingHistory() {
        TenantContext editor = surveys.member(tenantA, "departing-editor", "editor", surveyA.id());
        assertThat(recentWork.upsert(tenantA.tenant(), editor.actorId(),
                new RecentWorkCommand(surveyA.id(), DashboardPage.EDIT, null), now)).isTrue();

        members.remove(tenantA.owner(), editor.actorId());

        assertThat(recentWork.findVisible(tenantA.tenant(), editor.actorId(), 50)).isEmpty();
        assertThat(rawRowCount(tenantA.tenant(), editor.actorId())).isOne();
    }

    @Test
    void pageSpecificAccessIsRecheckedWhenRecordingAndReadingRecentWork() {
        TenantContext actor = access.activeMember(tenantA.owner(), "recent-work-reader");
        UUID editorGrant = access.grants().grant(tenantA.owner(),
                new GrantRequest(actor.actorId(), "editor", surveyA.id(), null));
        Instant visited = now;
        for (DashboardPage page : DashboardPage.values()) {
            Integer version = page == DashboardPage.VERSION ? 1 : null;
            assertThat(recentWork.upsert(tenantA.tenant(), actor.actorId(),
                    new RecentWorkCommand(surveyA.id(), page, version), visited)).isTrue();
            visited = visited.plusSeconds(1);
        }

        access.grants().revoke(tenantA.owner(), editorGrant);
        access.grants().grant(tenantA.owner(),
                new GrantRequest(actor.actorId(), "publish_reviewer", surveyA.id(), null));

        for (DashboardPage page : DashboardPage.values()) {
            Integer version = page == DashboardPage.VERSION ? 2 : null;
            assertThat(recentWork.upsert(tenantA.tenant(), actor.actorId(),
                    new RecentWorkCommand(surveyA.id(), page, version), visited))
                    .as("page %s should follow its route permission", page)
                    .isEqualTo(page == DashboardPage.VERSION);
        }
        assertThat(recentWork.findVisible(tenantA.tenant(), actor.actorId(), 50))
                .extracting(RecentWorkView::page, RecentWorkView::targetPath)
                .containsExactly(
                        org.assertj.core.groups.Tuple.tuple(DashboardPage.VERSION,
                                "/surveys/" + surveyA.id() + "/versions/2"),
                        org.assertj.core.groups.Tuple.tuple(DashboardPage.VERSION,
                                "/surveys/" + surveyA.id() + "/versions/1"));
        assertThat(rawRowCount(tenantA.tenant(), actor.actorId()))
                .isEqualTo(DashboardPage.values().length + 1L);
    }

    @Test
    void archivingTheSurveyOrAnAncestorFiltersStoredWork() {
        assertThat(recentWork.upsert(tenantA.tenant(), tenantA.owner().actorId(),
                new RecentWorkCommand(surveyA.id(), DashboardPage.EDIT, null), now)).isTrue();

        resourceTree.archive(tenantA.owner(), tenantA.project());

        assertThat(recentWork.findVisible(tenantA.tenant(), tenantA.owner().actorId(), 50)).isEmpty();
        assertThat(rawRowCount(tenantA.tenant(), tenantA.owner().actorId())).isOne();
    }

    @Test
    void rowLevelSecurityRejectsWritingAnotherTenantsIdentity() {
        assertThatThrownBy(() -> tenantScope.run(tenantB.tenant(), () -> jdbc.sql("""
                        INSERT INTO dashboard_recent_work
                            (tenant_id, actor_id, survey_id, page, version_no, visited_at)
                        VALUES (:tenantA, :actor, :survey, 'edit', NULL, :visited)
                        """)
                .param("tenantA", tenantA.tenant().value())
                .param("actor", tenantA.owner().actorId())
                .param("survey", surveyA.id())
                .param("visited", Timestamp.from(now))
                .update()))
                .rootCause().hasMessageContaining("row-level security");
    }

    @Test
    void databaseRejectsUnknownPagesAndInvalidVersionCombinations() {
        assertThatThrownBy(() -> insertRaw("settings", null))
                .rootCause().hasMessageContaining("dashboard_recent_work_page_check");
        assertThatThrownBy(() -> insertRaw("version", null))
                .rootCause().hasMessageContaining("dashboard_recent_work_check");
        assertThatThrownBy(() -> insertRaw("version", 0))
                .rootCause().hasMessageContaining("dashboard_recent_work_check");
        assertThatThrownBy(() -> insertRaw("edit", 1))
                .rootCause().hasMessageContaining("dashboard_recent_work_check");
    }

    private void insertRaw(String page, Integer version) {
        tenantScope.run(tenantA.tenant(), () -> jdbc.sql("""
                        INSERT INTO dashboard_recent_work
                            (tenant_id, actor_id, survey_id, page, version_no, visited_at)
                        VALUES (:tenant, :actor, :survey, :page, :version, :visited)
                        """)
                .param("tenant", tenantA.tenant().value())
                .param("actor", tenantA.owner().actorId())
                .param("survey", surveyA.id())
                .param("page", page)
                .param("version", version, Types.INTEGER)
                .param("visited", Timestamp.from(now))
                .update());
    }

    private long rawRowCount(TenantId tenant, String actorId) {
        return tenantScope.call(tenant, () -> jdbc.sql("""
                        SELECT count(*) FROM dashboard_recent_work
                        WHERE actor_id = :actor
                        """)
                .param("actor", actorId)
                .query(Long.class)
                .single());
    }
}
