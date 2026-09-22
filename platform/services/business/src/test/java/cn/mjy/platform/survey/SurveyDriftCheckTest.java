package cn.mjy.platform.survey;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import cn.mjy.platform.shared.TenantContext;
import cn.mjy.platform.shared.tenant.TenantScope;
import cn.mjy.platform.survey.SurveyFixture.Workspace;
import cn.mjy.platform.survey.gateway.DriftOutcome;
import cn.mjy.platform.survey.gateway.GatewayDriftRequest;
import java.util.List;
import java.util.UUID;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.jdbc.core.simple.JdbcClient;

/**
 * 漂移检测（WP-01 01.4，ADR 0012 决定 5）：有人在引擎管理端改了已发布的问卷。
 * 平台按版本发起检查、记录每次结论、漂移时告警与审计——但绝不自动重新发布或覆盖。
 */
@SpringBootTest
class SurveyDriftCheckTest {

    @Autowired
    private SurveyFixture fixture;

    @Autowired
    private SurveyPublishService publisher;

    @Autowired
    private SurveyService surveys;

    @Autowired
    private SurveyDriftService drift;

    @Autowired
    private FakePublishGateway gateway;

    @Autowired
    private TenantScope tenantScope;

    @Autowired
    private JdbcClient jdbc;

    private Workspace ws;
    private UUID survey;
    private PublishedVersionView v1;

    @BeforeEach
    void aPublishedSurvey() {
        ws = fixture.workspace();
        survey = fixture.newSurvey(ws).id();
        v1 = publisher.publish(ws.owner(), survey).version();
    }

    private long auditCount(String action) {
        return tenantScope.call(ws.tenant(), () -> jdbc
                .sql("SELECT COUNT(*) FROM audit_log WHERE action = :action AND resource LIKE :resource")
                .param("action", action)
                .param("resource", "survey/" + survey + "%")
                .query(Long.class).single());
    }

    private static DriftOutcome renamedInTheEngine(GatewayDriftRequest request) {
        return new DriftOutcome.Checked(true, "fm1:0e1235f2e2bc5a95", "Y",
                List.of(new DriftOutcome.Issue("E_CODE_DRIFT", "QSINGLE 被改成了 QDRIFTED")),
                List.of(new DriftOutcome.Rename("33333333-0001-4111-8111-000000000001", "QSINGLE", "QDRIFTED")));
    }

    @Test
    void anUntouchedVersionMatchesAndTheCheckIsRecorded() {
        DriftCheckView check = drift.check(ws.owner(), survey, 1);

        assertThat(check.outcome()).isEqualTo(DriftCheckView.MATCH);
        assertThat(check.expectedFingerprint()).isEqualTo(v1.fingerprint());
        assertThat(check.currentFingerprint()).isEqualTo(v1.fingerprint());
        assertThat(check.engineSid()).isEqualTo(v1.engineSid());
        assertThat(check.checkedBy()).isEqualTo(ws.owner().actorId());
        assertThat(drift.checks(ws.owner(), survey, 1)).containsExactly(check);
        assertThat(auditCount(SurveyDriftService.AUDIT_DRIFT)).isZero();
    }

    @Test
    void theGatewayIsAskedAboutExactlyThisVersionWithItsStoredBinding() {
        drift.check(ws.owner(), survey, 1);

        GatewayDriftRequest request = gateway.driftCallsFor(v1.engineSid()).getFirst();
        assertThat(request.engineInstanceId()).isEqualTo(v1.engineInstanceId());
        assertThat(request.expectedFingerprint()).isEqualTo(v1.fingerprint());
        assertThat(fixture.parse(request.bindingJson()).get("surveyId").asInt()).isEqualTo(v1.engineSid());
    }

    @Test
    void anEngineSideEditIsFlaggedAuditedAndNeverOverwritten() {
        gateway.scriptDrift(v1.engineSid(), SurveyDriftCheckTest::renamedInTheEngine);

        DriftCheckView check = drift.check(ws.owner(), survey, 1);

        assertThat(check.outcome()).isEqualTo(DriftCheckView.DRIFT);
        assertThat(check.currentFingerprint()).isEqualTo("fm1:0e1235f2e2bc5a95");
        assertThat(check.issues()).extracting(DriftCheckView.Issue::code).containsExactly("E_CODE_DRIFT");
        assertThat(check.renamed()).singleElement()
                .satisfies(r -> assertThat(r.to()).isEqualTo("QDRIFTED"));
        assertThat(auditCount(SurveyDriftService.AUDIT_DRIFT)).isEqualTo(1);
        // 绝不自动覆盖：没有新的发布、版本与路由原样。
        assertThat(gateway.callsFor(survey)).hasSize(1);
        assertThat(surveys.get(ws.owner(), survey).status()).isEqualTo(SurveyStatus.PUBLISHED);
        assertThat(surveys.versions(ws.owner(), survey)).hasSize(1);
    }

    @Test
    void anUnreachableEngineIsRecordedAsAnErrorNotAsAMatch() {
        gateway.scriptDrift(v1.engineSid(), request -> new DriftOutcome.Unavailable("gateway returned http 502"));

        DriftCheckView check = drift.check(ws.owner(), survey, 1);

        assertThat(check.outcome()).isEqualTo(DriftCheckView.ERROR);
        assertThat(check.currentFingerprint()).isNull();
        assertThat(check.detail()).contains("502");
    }

    @Test
    void checksAreListedNewestFirst() {
        drift.check(ws.owner(), survey, 1);
        gateway.scriptDrift(v1.engineSid(), SurveyDriftCheckTest::renamedInTheEngine);
        drift.check(ws.owner(), survey, 1);

        assertThat(drift.checks(ws.owner(), survey, 1)).extracting(DriftCheckView::outcome)
                .containsExactly(DriftCheckView.DRIFT, DriftCheckView.MATCH);
    }

    @Test
    void anUnknownVersionIsNotFound() {
        assertThatThrownBy(() -> drift.check(ws.owner(), survey, 9)).isInstanceOf(SurveyNotFoundException.class);
    }

    @Test
    void aStatisticsViewerMayReadChecksButNotTriggerThem() {
        drift.check(ws.owner(), survey, 1);
        TenantContext viewer = fixture.member(ws, "stats", "statistics_viewer", survey);

        assertThat(drift.checks(viewer, survey, 1)).hasSize(1);
        assertThatThrownBy(() -> drift.check(viewer, survey, 1)).isInstanceOf(SurveyAccessDeniedException.class);
    }

    @Test
    void anotherTenantSeesNeitherTheVersionNorItsChecks() {
        drift.check(ws.owner(), survey, 1);
        Workspace other = fixture.workspace();

        assertThatThrownBy(() -> drift.check(other.owner(), survey, 1)).isInstanceOf(SurveyNotFoundException.class);
        assertThatThrownBy(() -> drift.checks(other.owner(), survey, 1))
                .isInstanceOf(SurveyNotFoundException.class);
        long visible = tenantScope.call(other.tenant(), () -> jdbc
                .sql("SELECT COUNT(*) FROM survey_drift_check WHERE survey_id = :id")
                .param("id", survey).query(Long.class).single());
        assertThat(visible).isZero();
        assertThatThrownBy(() -> tenantScope.run(other.tenant(), () -> jdbc.sql("""
                        INSERT INTO survey_drift_check (tenant_id, survey_id, version_no, engine_instance_id,
                            engine_sid, expected_fingerprint, outcome, checked_by)
                        VALUES (:tenant, :survey, 1, 'x', 1, 'fm1:00', 'match', 'intruder')
                        """)
                .param("tenant", ws.tenant().value()).param("survey", survey).update()))
                .rootCause().hasMessageContaining("row-level security");
    }

    @Test
    void recordedChecksCannotBeRewritten() {
        drift.check(ws.owner(), survey, 1);

        assertThatThrownBy(() -> tenantScope.run(ws.tenant(), () -> jdbc
                .sql("UPDATE survey_drift_check SET outcome = 'match' WHERE survey_id = :id")
                .param("id", survey).update()))
                .rootCause().hasMessageContaining("permission denied");
    }

    @Test
    void theScheduledMonitorChecksOnlyLiveVersions() {
        int changed = surveys.draft(ws.owner(), survey).version();
        surveys.saveDraft(ws.owner(), survey, changed, fixture.definitionTitled("第二版"));
        PublishedVersionView v2 = publisher.publish(ws.owner(), survey).version();

        assertThat(drift.checkLiveVersions(ws.tenant(), 10)).isEqualTo(1);

        assertThat(gateway.driftCallsFor(v2.engineSid())).hasSize(1);
        assertThat(gateway.driftCallsFor(v1.engineSid())).isEmpty();
        assertThat(drift.checks(ws.owner(), survey, 2)).singleElement()
                .satisfies(c -> assertThat(c.checkedBy()).isEqualTo(SurveyDriftService.SYSTEM_ACTOR));
    }

    @Test
    void theScheduledMonitorSkipsVersionsCheckedWithinTheInterval() {
        drift.check(ws.owner(), survey, 1);

        assertThat(drift.checkLiveVersions(ws.tenant(), 10)).isZero();
        assertThat(gateway.driftCallsFor(v1.engineSid())).hasSize(1);
    }
}
