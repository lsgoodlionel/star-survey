package cn.mjy.platform.survey;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import cn.mjy.platform.shared.TenantId;
import cn.mjy.platform.shared.tenant.TenantScope;
import cn.mjy.platform.survey.SurveyFixture.Workspace;
import cn.mjy.platform.survey.gateway.GatewayOutcome;
import cn.mjy.platform.survey.gateway.GatewayRequest;
import cn.mjy.platform.tenant.routing.SurveyRouteService;
import java.time.OffsetDateTime;
import java.util.List;
import java.util.UUID;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.context.ApplicationContext;
import org.springframework.jdbc.core.simple.JdbcClient;

/**
 * 待核对发布的后台核对：同一 requestId 原样重发、经既有收尾路径落地、退避、人工复核、
 * 与用户并发发布互斥，以及逐租户进入各自的行级安全作用域。测试里定时调度关闭，直接调用 runOnce。
 */
@SpringBootTest
class PublishReconciliationTest {

    @Autowired
    private SurveyFixture fixture;

    @Autowired
    private SurveyPublishService publisher;

    @Autowired
    private SurveyService surveys;

    @Autowired
    private PublishReconciler reconciler;

    @Autowired
    private PublishReconciliationProperties properties;

    @Autowired
    private FakePublishGateway gateway;

    @Autowired
    private SurveyRouteService routes;

    @Autowired
    private TenantScope tenantScope;

    @Autowired
    private JdbcClient jdbc;

    @Autowired
    private ApplicationContext context;

    private Workspace ws;
    private UUID survey;

    @BeforeEach
    void aDraftSurveyInATenantWithAnEngine() {
        ws = fixture.workspace();
        survey = fixture.newSurvey(ws).id();
    }

    /** 用户发布时网关结果未知：问卷进入待核对。 */
    private void pendingAfterUnknown(Workspace workspace, UUID id) {
        gateway.script(id, request -> new GatewayOutcome.Unknown("read timed out"));
        assertThat(publisher.publish(workspace.owner(), id).survey().status())
                .isEqualTo(SurveyStatus.PENDING_RECONCILIATION);
    }

    /** 网关其实已发布，应答丢失：问卷进入待核对，网关按 requestId 存档了成功结果。 */
    private void pendingAfterLostSuccess(Workspace workspace, UUID id) {
        gateway.timeoutAfterPublishing(id);
        assertThat(publisher.publish(workspace.owner(), id).survey().status())
                .isEqualTo(SurveyStatus.PENDING_RECONCILIATION);
    }

    private void makeDue(TenantId tenant, UUID id) {
        tenantScope.run(tenant, () -> jdbc.sql("""
                        UPDATE survey_publish_attempt SET next_reconcile_at = now() - interval '1 second'
                         WHERE request_id = (SELECT current_request_id FROM survey WHERE id = :id)
                        """)
                .param("id", id).update());
    }

    private void stuckPublishingFor(TenantId tenant, UUID id, String age) {
        tenantScope.run(tenant, () -> jdbc.sql("""
                        UPDATE survey SET status = 'publishing', publishing_started_at = now() - CAST(:age AS interval)
                         WHERE id = :id
                        """)
                .param("age", age).param("id", id).update());
    }

    private long auditCount(TenantId tenant, UUID id, String action, String actor) {
        return tenantScope.call(tenant, () -> jdbc.sql("""
                        SELECT COUNT(*) FROM audit_log
                         WHERE action = :action AND actor_id = :actor AND resource LIKE :resource
                        """)
                .param("action", action).param("actor", actor).param("resource", "survey/" + id + "%")
                .query(Long.class).single());
    }

    private PublishAttemptView lastPublish(Workspace workspace, UUID id) {
        return surveys.get(workspace.owner(), id).lastPublish();
    }

    @Test
    void aPendingPublishIsResentWithTheSameRequestAndSettledAsPublishedWithARoute() {
        pendingAfterLostSuccess(ws, survey);

        reconciler.runOnce();

        assertThat(surveys.get(ws.owner(), survey).status()).isEqualTo(SurveyStatus.PUBLISHED);
        List<GatewayRequest> calls = gateway.callsFor(survey);
        assertThat(calls).hasSize(2);
        assertThat(calls.get(1).requestId()).isEqualTo(calls.get(0).requestId());
        assertThat(calls.get(1).engineInstanceId()).isEqualTo(calls.get(0).engineInstanceId());
        // 定义快照原样重发（jsonb 存储会重排键序，按 JSON 语义比较；网关也按规范化后的内容比对）。
        assertThat(fixture.parse(calls.get(1).definitionJson())).isEqualTo(fixture.parse(calls.get(0).definitionJson()));
        assertThat(routes.findByPublicId(ws.tenant(), survey)).isPresent();
        assertThat(surveys.versions(ws.owner(), survey)).hasSize(1);
        assertThat(auditCount(ws.tenant(), survey, SurveyAudit.PUBLISH, PublishReconciler.SYSTEM_ACTOR))
                .isEqualTo(1);
    }

    @Test
    void aPendingPublishThatTheGatewayNowReportsAsFailedBecomesPublishFailedWithoutARoute() {
        pendingAfterUnknown(ws, survey);
        gateway.script(survey, request -> FakePublishGateway.rejected("question type not supported"));

        reconciler.runOnce();

        assertThat(surveys.get(ws.owner(), survey).status()).isEqualTo(SurveyStatus.PUBLISH_FAILED);
        assertThat(lastPublish(ws, survey).failures()).containsExactly("question type not supported");
        assertThat(routes.findByPublicId(ws.tenant(), survey)).isEmpty();
        assertThat(surveys.versions(ws.owner(), survey)).isEmpty();
        assertThat(auditCount(ws.tenant(), survey, SurveyAudit.PUBLISH_FAILED, PublishReconciler.SYSTEM_ACTOR))
                .isEqualTo(1);
    }

    @Test
    void aStillUnknownOutcomeStaysPendingAndBacksOffBeforeTheNextTry() {
        pendingAfterUnknown(ws, survey);

        reconciler.runOnce();

        assertThat(surveys.get(ws.owner(), survey).status()).isEqualTo(SurveyStatus.PENDING_RECONCILIATION);
        PublishAttemptView attempt = lastPublish(ws, survey);
        assertThat(attempt.tries()).isEqualTo(2);
        assertThat(attempt.nextReconcileAt()).isAfter(OffsetDateTime.now().plusSeconds(1));
        assertThat(attempt.manualReviewAt()).isNull();
        assertThat(gateway.callsFor(survey)).hasSize(2);

        reconciler.runOnce();

        assertThat(gateway.callsFor(survey)).hasSize(2);
        assertThat(lastPublish(ws, survey).tries()).isEqualTo(2);
    }

    @Test
    void theBackoffGrowsWithEachUnknownTry() {
        assertThat(reconciler.backoffAfter(1)).isEqualTo(properties.backoffInitial());
        assertThat(reconciler.backoffAfter(2)).isEqualTo(properties.backoffInitial().multipliedBy(2));
        assertThat(reconciler.backoffAfter(3)).isEqualTo(properties.backoffInitial().multipliedBy(4));
        assertThat(reconciler.backoffAfter(1_000)).isEqualTo(properties.backoffMax());
    }

    @Test
    void afterMaxUnknownAttemptsTheJobStopsRetryingAndFlagsTheAttemptForManualReview() {
        pendingAfterUnknown(ws, survey);
        for (int tries = 1; tries < properties.maxAttempts(); tries++) {
            makeDue(ws.tenant(), survey);
            reconciler.runOnce();
        }
        assertThat(gateway.callsFor(survey)).hasSize(properties.maxAttempts());
        assertThat(lastPublish(ws, survey).manualReviewAt()).isNull();

        makeDue(ws.tenant(), survey);
        reconciler.runOnce();
        makeDue(ws.tenant(), survey);
        reconciler.runOnce();

        assertThat(gateway.callsFor(survey)).hasSize(properties.maxAttempts());
        PublishAttemptView attempt = lastPublish(ws, survey);
        assertThat(attempt.manualReviewAt()).isNotNull();
        assertThat(attempt.tries()).isEqualTo(properties.maxAttempts());
        assertThat(surveys.get(ws.owner(), survey).status()).isEqualTo(SurveyStatus.PENDING_RECONCILIATION);
        assertThat(auditCount(ws.tenant(), survey, PublishReconciler.MANUAL_REVIEW_ACTION,
                PublishReconciler.SYSTEM_ACTOR)).isEqualTo(1);
    }

    @Test
    void aUserPublishWhileTheJobIsAtTheGatewayGetsInProgressAndOnlyOneResultIsRecorded() throws Exception {
        pendingAfterLostSuccess(ws, survey);
        gateway.hold(survey);
        Thread job = Thread.ofVirtual().start(reconciler::runOnce);
        try {
            assertThat(gateway.awaitCalls(survey, 2, 10)).isTrue();
            assertThatThrownBy(() -> publisher.publish(ws.owner(), survey))
                    .isInstanceOfSatisfying(SurveyConflictException.class,
                            e -> assertThat(e.code()).isEqualTo(SurveyConflictException.PUBLISH_IN_PROGRESS));
        } finally {
            gateway.release(survey);
        }
        job.join(20_000);

        assertThat(gateway.callsFor(survey)).hasSize(2);
        assertThat(surveys.get(ws.owner(), survey).status()).isEqualTo(SurveyStatus.PUBLISHED);
        assertThat(surveys.versions(ws.owner(), survey)).hasSize(1);
        assertThat(auditCount(ws.tenant(), survey, SurveyAudit.PUBLISH, PublishReconciler.SYSTEM_ACTOR))
                .isEqualTo(1);
    }

    @Test
    void theJobSkipsASurveyWhoseReconciliationAUserIsAlreadyRunning() throws Exception {
        pendingAfterLostSuccess(ws, survey);
        gateway.hold(survey);
        Thread user = Thread.ofVirtual().start(() -> publisher.publish(ws.owner(), survey));
        try {
            assertThat(gateway.awaitCalls(survey, 2, 10)).isTrue();
            reconciler.runOnce();
            assertThat(gateway.callsFor(survey)).hasSize(2);
        } finally {
            gateway.release(survey);
        }
        user.join(20_000);

        assertThat(gateway.callsFor(survey)).hasSize(2);
        assertThat(surveys.get(ws.owner(), survey).status()).isEqualTo(SurveyStatus.PUBLISHED);
        assertThat(surveys.versions(ws.owner(), survey)).hasSize(1);
        assertThat(auditCount(ws.tenant(), survey, SurveyAudit.PUBLISH, PublishReconciler.SYSTEM_ACTOR))
                .isZero();
    }

    @Test
    void theJobSettlesEachTenantsSurveysInThatTenantsOwnScope() {
        Workspace other = fixture.workspace();
        UUID otherSurvey = fixture.newSurvey(other).id();
        pendingAfterLostSuccess(ws, survey);
        pendingAfterLostSuccess(other, otherSurvey);

        reconciler.runOnce();

        assertThat(surveys.get(ws.owner(), survey).status()).isEqualTo(SurveyStatus.PUBLISHED);
        assertThat(surveys.get(other.owner(), otherSurvey).status()).isEqualTo(SurveyStatus.PUBLISHED);
        assertThat(routes.findByPublicId(ws.tenant(), survey)).isPresent();
        assertThat(routes.findByPublicId(other.tenant(), otherSurvey)).isPresent();
        assertThat(routes.findByPublicId(other.tenant(), survey)).isEmpty();
        assertThat(routes.findByPublicId(ws.tenant(), otherSurvey)).isEmpty();
        assertThat(auditCount(ws.tenant(), survey, SurveyAudit.PUBLISH, PublishReconciler.SYSTEM_ACTOR))
                .isEqualTo(1);
        assertThat(auditCount(other.tenant(), otherSurvey, SurveyAudit.PUBLISH, PublishReconciler.SYSTEM_ACTOR))
                .isEqualTo(1);
        // 审计写在所属租户：从另一个租户的作用域里看不到。
        assertThat(auditCount(other.tenant(), survey, SurveyAudit.PUBLISH, PublishReconciler.SYSTEM_ACTOR))
                .isZero();
    }

    @Test
    void aPublishStuckLongerThanTheStaleThresholdIsPickedUpButAYoungerOneIsNot() {
        UUID young = fixture.newSurvey(ws).id();
        pendingAfterLostSuccess(ws, survey);
        pendingAfterLostSuccess(ws, young);
        stuckPublishingFor(ws.tenant(), survey, "11 minutes");
        stuckPublishingFor(ws.tenant(), young, "1 minute");

        reconciler.runOnce();

        assertThat(surveys.get(ws.owner(), survey).status()).isEqualTo(SurveyStatus.PUBLISHED);
        assertThat(gateway.callsFor(survey)).hasSize(2);
        assertThat(surveys.get(ws.owner(), young).status()).isEqualTo(SurveyStatus.PUBLISHING);
        assertThat(gateway.callsFor(young)).hasSize(1);
    }

    @Test
    void theSchedulerIsDisabledInTests() {
        assertThat(properties.enabled()).isFalse();
        assertThat(context.getBeanNamesForType(PublishReconciliationScheduler.class)).isEmpty();
    }
}
