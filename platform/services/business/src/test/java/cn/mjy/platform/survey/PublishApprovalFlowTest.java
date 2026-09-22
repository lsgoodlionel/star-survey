package cn.mjy.platform.survey;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import cn.mjy.platform.access.AccessFixture;
import cn.mjy.platform.access.DecisionReason;
import cn.mjy.platform.access.GrantRequest;
import cn.mjy.platform.shared.TenantContext;
import cn.mjy.platform.shared.tenant.TenantScope;
import cn.mjy.platform.survey.SurveyFixture.Workspace;
import cn.mjy.platform.survey.gateway.GatewayRequest;
import java.util.List;
import java.util.UUID;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.jdbc.core.simple.JdbcClient;

/**
 * 发布审批（R01-09，ADR 0011）：批准绑定提交时的草稿版本，改稿即作废；批准后由有发布权的人发布，
 * 发布的正是被批准的版本；审批人须对问卷或其上级拥有 approve-publish，自批仅在本人拥有该权限时允许。
 */
@SpringBootTest
class PublishApprovalFlowTest {

    private static final String APPROVED_TITLE = "已批准的标题";

    @Autowired
    private SurveyFixture fixture;

    @Autowired
    private AccessFixture accessFixture;

    @Autowired
    private SurveyService surveys;

    @Autowired
    private SurveyPublishService publisher;

    @Autowired
    private PublishApprovalService approvals;

    @Autowired
    private FakePublishGateway gateway;

    @Autowired
    private TenantScope tenantScope;

    @Autowired
    private JdbcClient jdbc;

    private Workspace ws;
    private UUID survey;
    private TenantContext editor;
    private TenantContext reviewer;

    @BeforeEach
    void aSubAccountEditorAndAProjectReviewerInATenantThatRequiresApproval() {
        ws = fixture.workspace();
        survey = fixture.newSurvey(ws).id();
        fixture.requirePublishApproval(ws, true);
        editor = fixture.member(ws, "sub-editor", "editor", survey);
        // 授权挂在项目（问卷的上级）上：审批权沿资源树继承。
        reviewer = fixture.member(ws, "reviewer", "publish_reviewer", ws.project());
    }

    private int saveDraftTitled(TenantContext ctx, String title) {
        int current = surveys.draft(ws.owner(), survey).version();
        return surveys.saveDraft(ctx, survey, current, fixture.definitionTitled(title)).version();
    }

    private UUID approvedRequestFor(int draftVersion) {
        UUID id = approvals.submit(editor, survey, draftVersion).id();
        approvals.approve(reviewer, id);
        return id;
    }

    private void assertApprovalRequired(TenantContext ctx) {
        assertThatThrownBy(() -> publisher.publish(ctx, survey))
                .isInstanceOfSatisfying(SurveyAccessDeniedException.class,
                        e -> assertThat(e.reason()).isEqualTo(DecisionReason.APPROVAL_REQUIRED));
    }

    private String titleSentToGateway(GatewayRequest request) {
        return fixture.parse(request.definitionJson()).get("title").asString();
    }

    private long auditCount(String action) {
        return tenantScope.call(ws.tenant(), () -> jdbc
                .sql("SELECT COUNT(*) FROM audit_log WHERE action = :action AND resource LIKE :resource")
                .param("action", action)
                .param("resource", "survey/" + survey + "%")
                .query(Long.class).single());
    }

    @Test
    void aSubAccountCannotPublishWhileItsRequestIsOnlyPending() {
        approvals.submit(editor, survey, 1);

        assertApprovalRequired(editor);
        assertThat(gateway.callsFor(survey)).isEmpty();
    }

    @Test
    void anApprovedRequestLetsTheApplicantPublishExactlyTheApprovedVersion() {
        int approvedVersion = saveDraftTitled(editor, APPROVED_TITLE);
        UUID request = approvedRequestFor(approvedVersion);

        PublishOutcome outcome = publisher.publish(editor, survey);

        assertThat(outcome.survey().status()).isEqualTo(SurveyStatus.PUBLISHED);
        assertThat(outcome.version().draftVersion()).isEqualTo(approvedVersion);
        List<GatewayRequest> calls = gateway.callsFor(survey);
        assertThat(calls).hasSize(1);
        assertThat(titleSentToGateway(calls.getFirst())).isEqualTo(APPROVED_TITLE);
        assertThat(approvals.get(editor, request).request().status()).isEqualTo(PublishApprovalStatus.PUBLISHED);
    }

    @Test
    void anyoneWithPublishOnTheSurveyMayTriggerThePublishAfterApproval() {
        approvedRequestFor(1);
        TenantContext colleague = fixture.member(ws, "colleague", "editor", survey);

        assertThat(publisher.publish(colleague, survey).survey().status()).isEqualTo(SurveyStatus.PUBLISHED);
    }

    @Test
    void anApprovalDoesNotLetSomeoneWithoutPublishRightsPublish() {
        approvedRequestFor(1);

        assertThatThrownBy(() -> publisher.publish(reviewer, survey))
                .isInstanceOfSatisfying(SurveyAccessDeniedException.class,
                        e -> assertThat(e.reason()).isEqualTo(DecisionReason.NO_MATCHING_GRANT));
        assertThat(gateway.callsFor(survey)).isEmpty();
    }

    @Test
    void editingTheDraftAfterApprovalVoidsTheApprovalAndPublishIsRefused() {
        UUID request = approvedRequestFor(1);

        saveDraftTitled(editor, "批准后又改了");

        assertThat(approvals.get(editor, request).request().status()).isEqualTo(PublishApprovalStatus.VOIDED);
        assertApprovalRequired(editor);
        assertThat(gateway.callsFor(survey)).isEmpty();
        assertThat(surveys.get(ws.owner(), survey).status()).isEqualTo(SurveyStatus.DRAFT);
    }

    @Test
    void editingTheDraftWhilePendingVoidsTheRequestSoItCanNoLongerBeApproved() {
        UUID request = approvals.submit(editor, survey, 1).id();
        saveDraftTitled(editor, "审批中改稿");

        assertThatThrownBy(() -> approvals.approve(reviewer, request))
                .isInstanceOfSatisfying(SurveyConflictException.class,
                        e -> assertThat(e.code()).isEqualTo("approval_not_pending"));
        assertApprovalRequired(editor);
    }

    @Test
    void aRequestMustNameTheCurrentDraftVersion() {
        assertThatThrownBy(() -> approvals.submit(editor, survey, 99))
                .isInstanceOfSatisfying(SurveyConflictException.class,
                        e -> assertThat(e.code()).isEqualTo("version_conflict"));
    }

    @Test
    void aSurveyHasAtMostOneOpenRequest() {
        UUID first = approvals.submit(editor, survey, 1).id();

        assertThatThrownBy(() -> approvals.submit(editor, survey, 1))
                .isInstanceOfSatisfying(SurveyConflictException.class,
                        e -> assertThat(e.code()).isEqualTo("approval_already_open"));

        approvals.reject(reviewer, first, "措辞需要调整");
        assertThat(approvals.submit(editor, survey, 1).status()).isEqualTo(PublishApprovalStatus.PENDING);
    }

    @Test
    void aRejectionRecordsTheReasonAndKeepsPublishBlocked() {
        UUID request = approvals.submit(editor, survey, 1).id();

        assertThatThrownBy(() -> approvals.reject(reviewer, request, "  "))
                .isInstanceOf(InvalidSurveyRequestException.class);
        PublishApprovalView rejected = approvals.reject(reviewer, request, "措辞需要调整");

        assertThat(rejected.status()).isEqualTo(PublishApprovalStatus.REJECTED);
        assertThat(rejected.reason()).isEqualTo("措辞需要调整");
        assertThat(rejected.decidedBy()).isEqualTo("reviewer");
        assertApprovalRequired(editor);
        assertThatThrownBy(() -> approvals.approve(reviewer, request))
                .isInstanceOfSatisfying(SurveyConflictException.class,
                        e -> assertThat(e.code()).isEqualTo("approval_not_pending"));
    }

    @Test
    void onlyTheApplicantMayWithdrawAndAWithdrawnApprovalNoLongerCounts() {
        UUID request = approvedRequestFor(1);

        assertThatThrownBy(() -> approvals.withdraw(reviewer, request))
                .isInstanceOf(PublishApprovalForbiddenException.class);
        assertThat(approvals.withdraw(editor, request).status()).isEqualTo(PublishApprovalStatus.WITHDRAWN);

        assertApprovalRequired(editor);
        assertThat(approvals.submit(editor, survey, 1).status()).isEqualTo(PublishApprovalStatus.PENDING);
    }

    @Test
    void anApproverWithoutApprovePublishOnTheSurveyOrAnAncestorIsRefused() {
        UUID request = approvals.submit(editor, survey, 1).id();
        TenantContext colleague = fixture.member(ws, "colleague", "editor", survey);
        UUID otherSurvey = fixture.newSurvey(ws).id();
        TenantContext elsewhere = fixture.member(ws, "elsewhere", "publish_reviewer", otherSurvey);

        for (TenantContext approver : List.of(colleague, elsewhere)) {
            assertThatThrownBy(() -> approvals.approve(approver, request))
                    .as(approver.actorId())
                    .isInstanceOfSatisfying(SurveyAccessDeniedException.class,
                            e -> assertThat(e.reason()).isEqualTo(DecisionReason.NO_MATCHING_GRANT));
            assertThatThrownBy(() -> approvals.reject(approver, request, "不行"))
                    .isInstanceOf(SurveyAccessDeniedException.class);
        }
        assertThat(approvals.get(editor, request).request().status()).isEqualTo(PublishApprovalStatus.PENDING);
    }

    @Test
    void selfApprovalIsAllowedOnlyWhenTheApplicantHoldsApprovePublish() {
        UUID request = approvals.submit(editor, survey, 1).id();
        assertThatThrownBy(() -> approvals.approve(editor, request))
                .isInstanceOf(SurveyAccessDeniedException.class);

        accessFixture.grants().grant(ws.owner(), new GrantRequest("sub-editor", "publish_reviewer", survey, null));
        PublishApprovalView approved = approvals.approve(editor, request);

        assertThat(approved.status()).isEqualTo(PublishApprovalStatus.APPROVED);
        assertThat(approved.decidedBy()).isEqualTo(approved.applicant());
        assertThat(publisher.publish(editor, survey).survey().status()).isEqualTo(SurveyStatus.PUBLISHED);
    }

    @Test
    void submittingRequiresPublishOnTheSurvey() {
        TenantContext viewer = fixture.member(ws, "stats", "statistics_viewer", survey);

        for (TenantContext applicant : List.of(reviewer, viewer)) {
            assertThatThrownBy(() -> approvals.submit(applicant, survey, 1))
                    .as(applicant.actorId())
                    .isInstanceOfSatisfying(SurveyAccessDeniedException.class,
                            e -> assertThat(e.reason()).isEqualTo(DecisionReason.NO_MATCHING_GRANT));
        }
    }

    @Test
    void theApprovalStaysValidAcrossFailedPublishRetries() {
        UUID request = approvedRequestFor(1);
        gateway.script(survey, r -> FakePublishGateway.rejected("E_TEMPORARY"));

        assertThat(publisher.publish(editor, survey).survey().status()).isEqualTo(SurveyStatus.PUBLISH_FAILED);
        assertThat(approvals.get(editor, request).request().status()).isEqualTo(PublishApprovalStatus.APPROVED);

        gateway.script(survey, gateway::success);
        PublishOutcome retried = publisher.publish(editor, survey);

        assertThat(retried.survey().status()).isEqualTo(SurveyStatus.PUBLISHED);
        assertThat(retried.version().draftVersion()).isEqualTo(1);
        assertThat(approvals.get(editor, request).request().status()).isEqualTo(PublishApprovalStatus.PUBLISHED);
    }

    @Test
    void aReconciliationRetryResendsTheApprovedSnapshotEvenAfterTheDraftChanged() {
        int approvedVersion = saveDraftTitled(editor, APPROVED_TITLE);
        approvedRequestFor(approvedVersion);
        gateway.timeoutAfterPublishing(survey);
        assertThat(publisher.publish(editor, survey).survey().status())
                .isEqualTo(SurveyStatus.PENDING_RECONCILIATION);

        saveDraftTitled(editor, "结果未知时改稿");
        PublishOutcome resolved = publisher.publish(editor, survey);

        assertThat(resolved.survey().status()).isEqualTo(SurveyStatus.PUBLISHED);
        assertThat(resolved.version().draftVersion()).isEqualTo(approvedVersion);
        List<GatewayRequest> calls = gateway.callsFor(survey);
        assertThat(calls).hasSize(2);
        assertThat(calls.get(1).requestId()).isEqualTo(calls.get(0).requestId());
        assertThat(titleSentToGateway(calls.get(1))).isEqualTo(APPROVED_TITLE);
    }

    @Test
    void thePendingListShowsOnlyRequestsTheCallerMayApprove() {
        UUID sibling = fixture.newSurvey(ws).id();
        TenantContext siblingEditor = fixture.member(ws, "sibling-editor", "editor", sibling);
        TenantContext narrowReviewer = fixture.member(ws, "narrow", "publish_reviewer", survey);
        UUID mine = approvals.submit(editor, survey, 1).id();
        UUID theirs = approvals.submit(siblingEditor, sibling, 1).id();

        assertThat(approvals.pendingForApprover(reviewer)).extracting(PublishApprovalView::id)
                .contains(mine, theirs);
        assertThat(approvals.pendingForApprover(narrowReviewer)).extracting(PublishApprovalView::id)
                .containsExactly(mine);
        assertThat(approvals.pendingForApprover(editor)).isEmpty();

        approvals.approve(reviewer, mine);
        assertThat(approvals.pendingForApprover(narrowReviewer)).isEmpty();
    }

    @Test
    void everyStepIsAuditedAndKeptInTheDecisionHistory() {
        UUID request = approvedRequestFor(1);
        publisher.publish(editor, survey);

        assertThat(auditCount("survey.approval.submit")).isEqualTo(1);
        assertThat(auditCount("survey.approval.approve")).isEqualTo(1);
        assertThat(auditCount("survey.approval.publish")).isEqualTo(1);
        PublishApprovalDetail detail = approvals.get(editor, request);
        assertThat(detail.history()).extracting(PublishApprovalEventView::event)
                .containsExactly("submitted", "approved", "publish_started", "published");
        assertThat(detail.history().get(2).publishRequestId())
                .isEqualTo(gateway.callsFor(survey).getFirst().requestId());
        assertThat(approvals.forSurvey(editor, survey)).extracting(PublishApprovalView::id).containsExactly(request);
    }

    @Test
    void membersWhoMayBypassStillPublishWithoutAnyRequest() {
        assertThat(publisher.publish(ws.owner(), survey).survey().status()).isEqualTo(SurveyStatus.PUBLISHED);
    }

    @Test
    void anotherTenantCanNeitherSeeNorDecideThisTenantsRequests() {
        UUID request = approvals.submit(editor, survey, 1).id();
        Workspace other = fixture.workspace();

        assertThatThrownBy(() -> approvals.approve(other.owner(), request)).isInstanceOf(SurveyNotFoundException.class);
        assertThatThrownBy(() -> approvals.reject(other.owner(), request, "x"))
                .isInstanceOf(SurveyNotFoundException.class);
        assertThatThrownBy(() -> approvals.withdraw(other.owner(), request))
                .isInstanceOf(SurveyNotFoundException.class);
        assertThatThrownBy(() -> approvals.get(other.owner(), request)).isInstanceOf(SurveyNotFoundException.class);
        assertThatThrownBy(() -> approvals.submit(other.owner(), survey, 1))
                .isInstanceOf(SurveyNotFoundException.class);
        assertThat(approvals.pendingForApprover(other.owner())).isEmpty();
        assertThat(approvals.get(editor, request).request().status()).isEqualTo(PublishApprovalStatus.PENDING);
    }
}
