package cn.mjy.platform.survey;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import cn.mjy.platform.access.DecisionReason;
import cn.mjy.platform.shared.TenantContext;
import cn.mjy.platform.shared.tenant.TenantScope;
import cn.mjy.platform.survey.SurveyFixture.Workspace;
import cn.mjy.platform.survey.gateway.GatewayCloseRequest;
import cn.mjy.platform.survey.gateway.GatewayRequest;
import cn.mjy.platform.tenant.routing.SurveyRoute;
import cn.mjy.platform.tenant.routing.SurveyRouteService;
import java.util.ArrayList;
import java.util.Collections;
import java.util.List;
import java.util.UUID;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.jdbc.core.simple.JdbcClient;

/**
 * 重新发布（WP-01 01.3，ADR 0012）：已上线问卷改稿后发布第 N+1 版＝网关新建一份引擎问卷；
 * 只有新版激活并落库之后才原子地切换公开路由，旧版随后收口（过期，不停用、不删除）。
 * 任何一步失败，旧版都继续在线、继续被路由。
 */
@SpringBootTest
class SurveyRepublishTest {

    @Autowired
    private SurveyFixture fixture;

    @Autowired
    private SurveyPublishService publisher;

    @Autowired
    private SurveyService surveys;

    @Autowired
    private PublishApprovalService approvals;

    @Autowired
    private PublishReconciler reconciler;

    @Autowired
    private SupersededVersionCloser closer;

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
    private PublishedVersionView v1;

    @BeforeEach
    void aPublishedSurvey() {
        ws = fixture.workspace();
        survey = fixture.newSurvey(ws).id();
        v1 = publisher.publish(ws.owner(), survey).version();
    }

    private int editDraft(TenantContext ctx, String title) {
        int current = surveys.draft(ws.owner(), survey).version();
        return surveys.saveDraft(ctx, survey, current, fixture.definitionTitled(title)).version();
    }

    private int currentRouteSid() {
        return routes.findByPublicId(ws.tenant(), survey).map(SurveyRoute::engineSid).orElseThrow();
    }

    private PublishedVersionView version(int no) {
        return surveys.version(ws.owner(), survey, no);
    }

    @Test
    void republishingAChangedDraftCreatesVersionTwoOnANewEngineSurveyAndSwitchesTheRoute() {
        editDraft(ws.owner(), "第二版");

        PublishOutcome outcome = publisher.publish(ws.owner(), survey);

        assertThat(outcome.survey().status()).isEqualTo(SurveyStatus.PUBLISHED);
        assertThat(outcome.survey().publishedVersion()).isEqualTo(2);
        PublishedVersionView v2 = outcome.version();
        assertThat(v2.version()).isEqualTo(2);
        assertThat(v2.engineSid()).isNotEqualTo(v1.engineSid());
        assertThat(currentRouteSid()).isEqualTo(v2.engineSid());
        assertThat(fixture.parse(gateway.callsFor(survey).get(1).definitionJson()).get("title").asString())
                .isEqualTo("第二版");
    }

    @Test
    void theOldVersionStaysImmutableQueryableAndStillResolvesItsResponses() {
        editDraft(ws.owner(), "第二版");
        PublishedVersionView v2 = publisher.publish(ws.owner(), survey).version();

        PublishedVersionView old = version(1);
        assertThat(old.engineSid()).isEqualTo(v1.engineSid());
        assertThat(old.fingerprint()).isEqualTo(v1.fingerprint());
        assertThat(old.fields()).isEqualTo(v1.fields());
        assertThat(old.definition()).isEqualTo(v1.definition());
        assertThat(old.live()).isFalse();
        assertThat(old.supersededAt()).isNotNull();
        assertThat(version(2).live()).isTrue();
        assertThat(version(2).supersededAt()).isNull();
        assertThat(surveys.versions(ws.owner(), survey)).extracting(PublishedVersionView::version)
                .containsExactly(1, 2);
        // 旧 sid 的答卷（引擎事件只带实例与 sid）仍能认回同一份问卷。
        assertThat(routes.findByEngine(ws.tenant(), v1.engineInstanceId(), v1.engineSid())).get()
                .extracting(SurveyRoute::publicId).isEqualTo(survey);
        assertThat(routes.findByEngine(ws.tenant(), v2.engineInstanceId(), v2.engineSid())).get()
                .extracting(SurveyRoute::publicId).isEqualTo(survey);
    }

    @Test
    void theOldEngineSurveyIsClosedOnlyAfterTheSwitchAndTheNewOneNever() {
        editDraft(ws.owner(), "第二版");
        PublishedVersionView v2 = publisher.publish(ws.owner(), survey).version();

        List<GatewayCloseRequest> closes = gateway.closeCallsFor(v1.engineSid());
        assertThat(closes).hasSize(1);
        assertThat(closes.getFirst().engineInstanceId()).isEqualTo(v1.engineInstanceId());
        assertThat(gateway.closeCallsFor(v2.engineSid())).isEmpty();
        assertThat(version(1).engineClosedAt()).isNotNull();
        assertThat(version(2).engineClosedAt()).isNull();
    }

    @Test
    void anUnchangedDraftIsStillAlreadyPublished() {
        assertThatThrownBy(() -> publisher.publish(ws.owner(), survey))
                .isInstanceOfSatisfying(SurveyConflictException.class,
                        e -> assertThat(e.code()).isEqualTo("already_published"));
        assertThat(gateway.callsFor(survey)).hasSize(1);
    }

    @Test
    void anActivationFailureKeepsTheOldVersionLiveAndRoutedAndAllowsARetry() {
        editDraft(ws.owner(), "第二版");
        gateway.script(survey, request -> FakePublishGateway.engineFailed(0, "activate_survey failed"));

        PublishOutcome failed = publisher.publish(ws.owner(), survey);

        assertThat(failed.survey().status()).isEqualTo(SurveyStatus.PUBLISH_FAILED);
        assertThat(failed.survey().publishedVersion()).isEqualTo(1);
        assertThat(currentRouteSid()).isEqualTo(v1.engineSid());
        assertThat(surveys.versions(ws.owner(), survey)).hasSize(1);
        assertThat(version(1).live()).isTrue();
        assertThat(gateway.closeCallsFor(v1.engineSid())).isEmpty();

        gateway.script(survey, gateway::success);
        PublishOutcome retried = publisher.publish(ws.owner(), survey);

        assertThat(retried.survey().publishedVersion()).isEqualTo(2);
        assertThat(currentRouteSid()).isEqualTo(retried.version().engineSid());
    }

    @Test
    void whileTheNextVersionIsPublishingTheOldOneIsStillRoutedAndAConcurrentRepublishLoses() throws Exception {
        editDraft(ws.owner(), "第二版");
        gateway.hold(survey);
        List<PublishOutcome> outcomes = Collections.synchronizedList(new ArrayList<>());
        Thread first = Thread.ofVirtual().start(() -> outcomes.add(publisher.publish(ws.owner(), survey)));
        try {
            assertThat(gateway.awaitEntered(survey, 10)).isTrue();

            SurveyView during = surveys.get(ws.owner(), survey);
            assertThat(during.status()).isEqualTo(SurveyStatus.PUBLISHING);
            assertThat(during.publishedVersion()).isEqualTo(1);
            assertThat(currentRouteSid()).isEqualTo(v1.engineSid());
            assertThatThrownBy(() -> publisher.publish(ws.owner(), survey))
                    .isInstanceOfSatisfying(SurveyConflictException.class,
                            e -> assertThat(e.code()).isEqualTo("publish_in_progress"));
        } finally {
            gateway.release(survey);
            first.join(20_000);
        }

        assertThat(outcomes).singleElement()
                .satisfies(o -> assertThat(o.survey().publishedVersion()).isEqualTo(2));
        assertThat(gateway.callsFor(survey)).hasSize(2);
        assertThat(surveys.versions(ws.owner(), survey)).hasSize(2);
    }

    @Test
    void anUnknownRepublishIsReconciledWithTheSameRequestAndOnlyThenSwitchesTheRoute() {
        editDraft(ws.owner(), "第二版");
        gateway.timeoutAfterPublishing(survey);

        PublishOutcome pending = publisher.publish(ws.owner(), survey);

        assertThat(pending.survey().status()).isEqualTo(SurveyStatus.PENDING_RECONCILIATION);
        assertThat(pending.survey().publishedVersion()).isEqualTo(1);
        assertThat(currentRouteSid()).isEqualTo(v1.engineSid());
        assertThat(gateway.closeCallsFor(v1.engineSid())).isEmpty();

        reconciler.runOnce();

        SurveyView settled = surveys.get(ws.owner(), survey);
        assertThat(settled.status()).isEqualTo(SurveyStatus.PUBLISHED);
        assertThat(settled.publishedVersion()).isEqualTo(2);
        List<GatewayRequest> calls = gateway.callsFor(survey);
        assertThat(calls).hasSize(3);
        assertThat(calls.get(2).requestId()).isEqualTo(calls.get(1).requestId());
        assertThat(currentRouteSid()).isEqualTo(version(2).engineSid());
        assertThat(gateway.closeCallsFor(v1.engineSid())).hasSize(1);
    }

    @Test
    void aFailedCloseLeavesTheSwitchInPlaceAndIsRetriedInTheBackground() {
        gateway.failClose(v1.engineSid());
        editDraft(ws.owner(), "第二版");

        PublishOutcome outcome = publisher.publish(ws.owner(), survey);

        assertThat(outcome.survey().status()).isEqualTo(SurveyStatus.PUBLISHED);
        assertThat(currentRouteSid()).isEqualTo(outcome.version().engineSid());
        assertThat(version(1).engineClosedAt()).isNull();

        gateway.allowClose(v1.engineSid());
        tenantScope.run(ws.tenant(), () -> jdbc
                .sql("UPDATE survey_version_retirement SET next_close_at = now() - interval '1 second' "
                        + "WHERE survey_id = :id")
                .param("id", survey).update());
        closer.runOnce();

        assertThat(version(1).engineClosedAt()).isNotNull();
        assertThat(gateway.closeCallsFor(v1.engineSid())).hasSize(2);
    }

    @Test
    void republishNeedsAnApprovalForTheChangedDraftAndADraftSaveVoidsIt() {
        fixture.requirePublishApproval(ws, true);
        TenantContext editor = fixture.member(ws, "sub-editor", "editor", survey);
        TenantContext reviewer = fixture.member(ws, "reviewer", "publish_reviewer", ws.project());
        int changed = editDraft(editor, "第二版");

        assertThatThrownBy(() -> publisher.publish(editor, survey))
                .isInstanceOfSatisfying(SurveyAccessDeniedException.class,
                        e -> assertThat(e.reason()).isEqualTo(DecisionReason.APPROVAL_REQUIRED));

        UUID request = approvals.submit(editor, survey, changed).id();
        approvals.approve(reviewer, request);
        int resaved = editDraft(editor, "第二版（再改）");

        assertThat(approvals.forSurvey(editor, survey)).filteredOn(a -> a.id().equals(request))
                .singleElement().satisfies(a -> assertThat(a.status()).isEqualTo(PublishApprovalStatus.VOIDED));
        assertThatThrownBy(() -> publisher.publish(editor, survey))
                .isInstanceOf(SurveyAccessDeniedException.class);
        assertThat(currentRouteSid()).isEqualTo(v1.engineSid());

        approvals.approve(reviewer, approvals.submit(editor, survey, resaved).id());
        PublishOutcome published = publisher.publish(editor, survey);

        assertThat(published.version().version()).isEqualTo(2);
        assertThat(published.version().draftVersion()).isEqualTo(resaved);
        assertThat(currentRouteSid()).isEqualTo(published.version().engineSid());
    }

    @Test
    void anApprovalCannotBeSubmittedForAnUnchangedPublishedDraft() {
        fixture.requirePublishApproval(ws, true);
        TenantContext editor = fixture.member(ws, "sub-editor", "editor", survey);
        int current = surveys.draft(ws.owner(), survey).version();

        assertThatThrownBy(() -> approvals.submit(editor, survey, current))
                .isInstanceOfSatisfying(SurveyConflictException.class,
                        e -> assertThat(e.code()).isEqualTo("already_published"));
    }
}
