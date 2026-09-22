package cn.mjy.platform.survey;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import cn.mjy.platform.access.DecisionReason;
import cn.mjy.platform.shared.TenantContext;
import cn.mjy.platform.shared.tenant.TenantScope;
import cn.mjy.platform.survey.SurveyFixture.Workspace;
import cn.mjy.platform.survey.gateway.GatewayOutcome;
import cn.mjy.platform.survey.gateway.GatewayRequest;
import cn.mjy.platform.tenant.routing.SurveyRoute;
import cn.mjy.platform.tenant.routing.SurveyRouteService;
import java.util.List;
import java.util.Optional;
import java.util.UUID;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.jdbc.core.simple.JdbcClient;

/** 发布流程：授权、选实例、状态机、网关应答的三类结局，以及路由登记与审计。 */
@SpringBootTest
class SurveyPublishTest {

    @Autowired
    private SurveyFixture fixture;

    @Autowired
    private SurveyPublishService publisher;

    @Autowired
    private SurveyService surveys;

    @Autowired
    private FakePublishGateway gateway;

    @Autowired
    private SurveyRouteService routes;

    @Autowired
    private TenantScope tenantScope;

    @Autowired
    private JdbcClient jdbc;

    private Workspace ws;
    private UUID survey;

    @BeforeEach
    void aDraftSurveyInATenantWithAnEngine() {
        ws = fixture.workspace();
        survey = fixture.newSurvey(ws).id();
    }

    private long auditCount(String action) {
        return tenantScope.call(ws.tenant(), () -> jdbc
                .sql("SELECT COUNT(*) FROM audit_log WHERE action = :action AND resource LIKE :resource")
                .param("action", action)
                .param("resource", "survey/" + survey + "%")
                .query(Long.class).single());
    }

    @Test
    void publishingStoresTheBindingRegistersTheRouteAndIsAudited() {
        PublishOutcome outcome = publisher.publish(ws.owner(), survey);

        assertThat(outcome.survey().status()).isEqualTo(SurveyStatus.PUBLISHED);
        PublishedVersionView version = outcome.version();
        assertThat(version.version()).isEqualTo(1);
        assertThat(version.engineInstanceId()).isEqualTo(ws.engineInstanceId());
        assertThat(version.fingerprint()).isEqualTo(FakePublishGateway.FINGERPRINT);
        assertThat(version.compilerVersion()).isEqualTo(FakePublishGateway.COMPILER_VERSION);
        assertThat(version.fields()).hasSize(5)
                .anySatisfy(q -> {
                    assertThat(q.questionUuid()).isEqualTo(UUID.fromString("33333333-0001-4111-8111-000000000001"));
                    assertThat(q.code()).isEqualTo("QSINGLE");
                    assertThat(q.fieldname()).startsWith("Q");
                });

        Optional<SurveyRoute> route = routes.findByPublicId(ws.tenant(), survey);
        assertThat(route).isPresent();
        assertThat(route.get().engineInstanceId()).isEqualTo(ws.engineInstanceId());
        assertThat(route.get().engineSid()).isEqualTo(version.engineSid());

        assertThat(auditCount("survey.publish")).isEqualTo(1);
        assertThat(surveys.versions(ws.owner(), survey)).hasSize(1);
    }

    @Test
    void theGatewayReceivesThePlatformRequestIdTheChosenInstanceAndTheSurveyUuid() {
        publisher.publish(ws.owner(), survey);

        List<GatewayRequest> calls = gateway.callsFor(survey);
        assertThat(calls).hasSize(1);
        assertThat(calls.getFirst().engineInstanceId()).isEqualTo(ws.engineInstanceId());
        assertThat(fixture.parse(calls.getFirst().definitionJson()).get("uuid").asString())
                .isEqualTo(survey.toString());
        assertThat(surveys.get(ws.owner(), survey).lastPublish().requestId())
                .isEqualTo(calls.getFirst().requestId());
    }

    @Test
    void aSubAccountWithoutApprovalIsDeniedWithTheApprovalReasonAndTheGatewayIsNeverCalled() {
        TenantContext editor = fixture.member(ws, "sub-editor", "editor", survey);
        fixture.requirePublishApproval(ws, true);

        assertThatThrownBy(() -> publisher.publish(editor, survey))
                .isInstanceOfSatisfying(SurveyAccessDeniedException.class,
                        e -> assertThat(e.reason()).isEqualTo(DecisionReason.APPROVAL_REQUIRED));

        assertThat(gateway.callsFor(survey)).isEmpty();
        assertThat(surveys.get(ws.owner(), survey).status()).isEqualTo(SurveyStatus.DRAFT);
    }

    @Test
    void aSubAccountMayPublishWhenTheTenantDoesNotRequireApproval() {
        TenantContext editor = fixture.member(ws, "sub-editor", "editor", survey);
        fixture.requirePublishApproval(ws, false);

        assertThat(publisher.publish(editor, survey).survey().status()).isEqualTo(SurveyStatus.PUBLISHED);
    }

    @Test
    void aStatisticsViewerCannotPublish() {
        TenantContext viewer = fixture.member(ws, "stats", "statistics_viewer", survey);
        fixture.requirePublishApproval(ws, false);

        assertThatThrownBy(() -> publisher.publish(viewer, survey))
                .isInstanceOfSatisfying(SurveyAccessDeniedException.class,
                        e -> assertThat(e.reason()).isEqualTo(DecisionReason.NO_MATCHING_GRANT));
        assertThat(gateway.callsFor(survey)).isEmpty();
    }

    @Test
    void anotherTenantCannotPublishThisSurvey() {
        Workspace other = fixture.workspace();

        assertThatThrownBy(() -> publisher.publish(other.owner(), survey))
                .isInstanceOf(SurveyNotFoundException.class);
        assertThat(gateway.callsFor(survey)).isEmpty();
        assertThat(surveys.get(ws.owner(), survey).status()).isEqualTo(SurveyStatus.DRAFT);
    }

    @Test
    void aTenantWithoutAnActiveEngineInstanceGetsAClearConflict() {
        Workspace bare = fixture.workspaceWithoutEngine();
        UUID lonely = fixture.newSurvey(bare).id();

        assertThatThrownBy(() -> publisher.publish(bare.owner(), lonely))
                .isInstanceOfSatisfying(SurveyConflictException.class,
                        e -> assertThat(e.code()).isEqualTo("no_active_engine_instance"));
        assertThat(surveys.get(bare.owner(), lonely).status()).isEqualTo(SurveyStatus.DRAFT);
    }

    @Test
    void aRejectedDefinitionLeavesNoRouteRecordsTheFailureAndAllowsARetry() {
        gateway.script(survey, request -> FakePublishGateway.rejected("E_MISSING_ANSWERS QSINGLE"));

        PublishOutcome failed = publisher.publish(ws.owner(), survey);

        assertThat(failed.survey().status()).isEqualTo(SurveyStatus.PUBLISH_FAILED);
        assertThat(failed.survey().lastPublish().failedStage()).isEqualTo("validate");
        assertThat(failed.survey().lastPublish().failures()).containsExactly("E_MISSING_ANSWERS QSINGLE");
        assertThat(failed.survey().lastPublish().gatewayStatus()).isEqualTo(422);
        assertThat(routes.findByPublicId(ws.tenant(), survey)).isEmpty();
        assertThat(surveys.versions(ws.owner(), survey)).isEmpty();
        assertThat(auditCount("survey.publish.failed")).isEqualTo(1);

        gateway.script(survey, gateway::success);
        PublishOutcome retried = publisher.publish(ws.owner(), survey);

        assertThat(retried.survey().status()).isEqualTo(SurveyStatus.PUBLISHED);
        List<GatewayRequest> calls = gateway.callsFor(survey);
        assertThat(calls).hasSize(2);
        assertThat(calls.get(1).requestId()).isNotEqualTo(calls.get(0).requestId());
    }

    @Test
    void anEngineFailureLeavesNoRouteAndSurfacesTheOrphanSurvey() {
        gateway.script(survey, request -> FakePublishGateway.engineFailed(9001, "ROLLBACK FAILED: delete_survey"));

        PublishOutcome failed = publisher.publish(ws.owner(), survey);

        assertThat(failed.survey().status()).isEqualTo(SurveyStatus.PUBLISH_FAILED);
        assertThat(failed.survey().lastPublish().gatewayStatus()).isEqualTo(502);
        assertThat(failed.survey().lastPublish().failedStage()).isEqualTo("activate");
        assertThat(failed.survey().lastPublish().orphanEngineSid()).isEqualTo(9001);
        assertThat(routes.findByPublicId(ws.tenant(), survey)).isEmpty();

        gateway.script(survey, gateway::success);
        assertThat(publisher.publish(ws.owner(), survey).survey().status()).isEqualTo(SurveyStatus.PUBLISHED);
    }

    @Test
    void anEngineFailureWithACleanRollbackAlsoAllowsARetry() {
        gateway.script(survey, request -> FakePublishGateway.engineFailed(0, "activate_survey failed"));

        PublishOutcome failed = publisher.publish(ws.owner(), survey);

        assertThat(failed.survey().status()).isEqualTo(SurveyStatus.PUBLISH_FAILED);
        assertThat(failed.survey().lastPublish().orphanEngineSid()).isNull();
        assertThat(routes.findByPublicId(ws.tenant(), survey)).isEmpty();
    }

    @Test
    void aTimeoutIsPendingReconciliationAndARetryWithTheSameRequestIdResolvesIt() {
        gateway.timeoutAfterPublishing(survey);

        PublishOutcome pending = publisher.publish(ws.owner(), survey);

        assertThat(pending.survey().status()).isEqualTo(SurveyStatus.PENDING_RECONCILIATION);
        assertThat(routes.findByPublicId(ws.tenant(), survey)).isEmpty();

        PublishOutcome resolved = publisher.publish(ws.owner(), survey);

        assertThat(resolved.survey().status()).isEqualTo(SurveyStatus.PUBLISHED);
        List<GatewayRequest> calls = gateway.callsFor(survey);
        assertThat(calls).hasSize(2);
        assertThat(calls.get(1).requestId()).isEqualTo(calls.get(0).requestId());
        assertThat(calls.get(1).engineInstanceId()).isEqualTo(calls.get(0).engineInstanceId());
        assertThat(routes.findByPublicId(ws.tenant(), survey)).isPresent();
        assertThat(surveys.versions(ws.owner(), survey)).hasSize(1);
    }

    @Test
    void aGatewayConflictIsTreatedAsAnUnknownOutcome() {
        gateway.script(survey, request -> new GatewayOutcome.Unknown("gateway 409 publish_in_progress"));

        assertThat(publisher.publish(ws.owner(), survey).survey().status())
                .isEqualTo(SurveyStatus.PENDING_RECONCILIATION);
    }

    @Test
    void aRefusedRequestIsAFailureThatNeverTouchedTheEngine() {
        gateway.script(survey, request -> new GatewayOutcome.Refused(404, "unknown_engine_instance"));

        PublishOutcome failed = publisher.publish(ws.owner(), survey);

        assertThat(failed.survey().status()).isEqualTo(SurveyStatus.PUBLISH_FAILED);
        assertThat(failed.survey().lastPublish().failedStage()).isEqualTo("gateway");
        assertThat(failed.survey().lastPublish().failures()).containsExactly("unknown_engine_instance");
    }

    @Test
    void aPublishedSurveyCannotBePublishedAgain() {
        publisher.publish(ws.owner(), survey);

        assertThatThrownBy(() -> publisher.publish(ws.owner(), survey))
                .isInstanceOfSatisfying(SurveyConflictException.class,
                        e -> assertThat(e.code()).isEqualTo("already_published"));
        assertThat(gateway.callsFor(survey)).hasSize(1);
    }

    @Test
    void aPublishThatDiedInFlightCanBeReconciledWithTheSameRequestIdOnceStale() {
        gateway.hold(survey);
        Thread stuck = Thread.ofVirtual().start(() -> publisher.publish(ws.owner(), survey));
        try {
            assertThat(gateway.awaitEntered(survey, 10)).isTrue();
            assertThatThrownBy(() -> publisher.publish(ws.owner(), survey))
                    .isInstanceOfSatisfying(SurveyConflictException.class,
                            e -> assertThat(e.code()).isEqualTo("publish_in_progress"));

            tenantScope.run(ws.tenant(), () -> jdbc
                    .sql("UPDATE survey SET publishing_started_at = now() - interval '1 hour' WHERE id = :id")
                    .param("id", survey).update());
            gateway.script(survey, gateway::success);
            Thread reconciler = Thread.ofVirtual().start(() -> publisher.publish(ws.owner(), survey));
            // 两个调用都停在网关里（同一 requestId）之后再放行，谁先收尾都只记录一次。
            assertThat(gateway.awaitCalls(survey, 2, 10)).isTrue();
            gateway.release(survey);
            reconciler.join(20_000);
            stuck.join(20_000);
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            throw new IllegalStateException(e);
        } finally {
            gateway.release(survey);
        }

        List<GatewayRequest> calls = gateway.callsFor(survey);
        assertThat(calls).hasSize(2);
        assertThat(calls.get(1).requestId()).isEqualTo(calls.get(0).requestId());
        assertThat(surveys.get(ws.owner(), survey).status()).isEqualTo(SurveyStatus.PUBLISHED);
        assertThat(surveys.versions(ws.owner(), survey)).hasSize(1);
    }
}
