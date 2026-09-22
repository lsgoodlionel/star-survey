package cn.mjy.platform.survey;

import static org.assertj.core.api.Assertions.assertThat;

import cn.mjy.platform.shared.TenantContext;
import cn.mjy.platform.survey.SurveyFixture.Workspace;
import cn.mjy.platform.survey.gateway.GatewayOutcome;
import cn.mjy.platform.survey.gateway.GatewayRequest;
import java.util.List;
import java.util.UUID;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;

/**
 * 审批与后台核对的交汇：经审批的发布结果未知时，后台核对以同一 requestId 落定，
 * 落定后审批记录必须闭合为 published，且发出的仍是被批准的那个草稿版本。
 */
@SpringBootTest
class PublishApprovalReconciliationTest {

    private static final String APPROVED_TITLE = "核对前已批准的标题";

    @Autowired
    private SurveyFixture fixture;

    @Autowired
    private SurveyService surveys;

    @Autowired
    private SurveyPublishService publisher;

    @Autowired
    private PublishApprovalService approvals;

    @Autowired
    private PublishReconciler reconciler;

    @Autowired
    private FakePublishGateway gateway;

    private Workspace ws;
    private UUID survey;
    private TenantContext editor;
    private TenantContext reviewer;

    @BeforeEach
    void anApprovalRequiredTenantWithASubAccountEditor() {
        ws = fixture.workspace();
        survey = fixture.newSurvey(ws).id();
        fixture.requirePublishApproval(ws, true);
        editor = fixture.member(ws, "sub-editor", "editor", survey);
        reviewer = fixture.member(ws, "reviewer", "publish_reviewer", ws.project());
    }

    private UUID approveVersionTitled(String title) {
        int current = surveys.draft(ws.owner(), survey).version();
        int version = surveys.saveDraft(editor, survey, current, fixture.definitionTitled(title)).version();
        UUID request = approvals.submit(editor, survey, version).id();
        approvals.approve(reviewer, request);
        return request;
    }

    @Test
    void theReconcilerSettlingAnApprovedPublishClosesTheApproval() {
        UUID request = approveVersionTitled(APPROVED_TITLE);
        gateway.timeoutAfterPublishing(survey);
        assertThat(publisher.publish(editor, survey).survey().status())
                .isEqualTo(SurveyStatus.PENDING_RECONCILIATION);
        assertThat(approvals.get(editor, request).request().status()).isEqualTo(PublishApprovalStatus.APPROVED);

        reconciler.runOnce();

        assertThat(surveys.get(ws.owner(), survey).status()).isEqualTo(SurveyStatus.PUBLISHED);
        assertThat(approvals.get(editor, request).request().status()).isEqualTo(PublishApprovalStatus.PUBLISHED);
        List<GatewayRequest> calls = gateway.callsFor(survey);
        assertThat(calls).hasSize(2);
        assertThat(calls.get(1).requestId()).isEqualTo(calls.get(0).requestId());
        assertThat(fixture.parse(calls.get(1).definitionJson()).get("title").asString()).isEqualTo(APPROVED_TITLE);
    }

    @Test
    void aReconciledFailureLeavesTheApprovalUsableForARetry() {
        UUID request = approveVersionTitled(APPROVED_TITLE);
        gateway.script(survey, r -> new GatewayOutcome.Unknown("read timed out"));
        publisher.publish(editor, survey);
        gateway.script(survey, r -> FakePublishGateway.engineFailed(0, "engine refused the import"));

        reconciler.runOnce();

        assertThat(surveys.get(ws.owner(), survey).status()).isEqualTo(SurveyStatus.PUBLISH_FAILED);
        assertThat(approvals.get(editor, request).request().status()).isEqualTo(PublishApprovalStatus.APPROVED);
    }
}
