package cn.mjy.platform.survey;

import cn.mjy.platform.access.DecisionReason;
import cn.mjy.platform.shared.TenantContext;
import cn.mjy.platform.survey.SurveyRepository.SurveyRow;
import java.util.List;
import java.util.UUID;
import org.springframework.stereotype.Component;

/**
 * 发布审批与发布 / 改稿流程的接缝。所有方法都在调用方的 {@code TenantScope} 事务内、持有问卷行锁之后调用
 * （{@link #clear} 除外，它只做权限判定）。
 *
 * <p><b>与 access 模块的分工</b>：权限只由 {@code AccessDecisionService} 判定，本类不改变、不放宽它的任何结论。
 * access 的结论只有三种走向：
 * <ul>
 *   <li>允许（审核开关关闭，或持有 publish-without-approval）→ 照旧直接发布，不看审批记录；</li>
 *   <li>APPROVAL_REQUIRED（在职成员、持有 publish，只差审核）→ 须有一条对当前草稿版本、状态为 approved 的申请；</li>
 *   <li>其他拒绝（无授权、非在职成员、资源不存在）→ 原样拒绝，任何批准都不能替代发布权。</li>
 * </ul>
 * 审批记录归本模块所有，放在 access 里会让 access 读写问卷表，违反模块边界；反过来，把"有批准就允许"
 * 做成 access 的规则则需要 access 了解草稿版本。因此由本模块在 access 给出 APPROVAL_REQUIRED 之后、
 * 在问卷行锁内核对批准，锁保证核对与改稿作废不会交错。
 *
 * <p>核对（pending_reconciliation / 陈旧 publishing 的同一 requestId 重发）不再要求批准：它只重发发起时已经
 * 通过闸门的那份定义快照（发起时按批准或免审放行），不会发布任何新内容；调用方仍须持有发布权。
 */
@Component
class PublishApprovalGate {

    static final String PUBLISH_STARTED = "publish_started";
    static final String PUBLISHED = "published";
    static final String VOIDED = "voided";

    static final String AUDIT_PUBLISH = "survey.approval.publish";
    static final String AUDIT_VOID = "survey.approval.void";

    private final SurveyAccess access;
    private final PublishApprovalRepository approvals;
    private final SurveyAudit audit;

    PublishApprovalGate(SurveyAccess access, PublishApprovalRepository approvals, SurveyAudit audit) {
        this.access = access;
        this.approvals = approvals;
        this.audit = audit;
    }

    /** 发布开始前的权限结论：needsApproval 为真表示调用方持有发布权但须凭批准发布。 */
    record Clearance(boolean needsApproval) {
    }

    Clearance clear(TenantContext ctx, UUID surveyId) {
        return new Clearance(access.requirePublishRight(ctx, surveyId));
    }

    /**
     * 新发起一次发布尝试之前调用（持问卷行锁）。须凭批准时，要求存在对当前草稿版本的 approved 申请，
     * 否则 403 APPROVAL_REQUIRED；通过则把这次尝试的 requestId 记到申请与决定历史上。
     */
    void authorizeFreshAttempt(TenantContext ctx, Clearance clearance, SurveyRow locked, UUID requestId) {
        if (!clearance.needsApproval()) {
            return;
        }
        PublishApprovalView approval = approvals.lockOpen(locked.id())
                .filter(open -> open.status() == PublishApprovalStatus.APPROVED
                        && open.draftVersion() == locked.draftVersion())
                .orElseThrow(() -> new SurveyAccessDeniedException(DecisionReason.APPROVAL_REQUIRED,
                        "publish requires an approved request for draft version " + locked.draftVersion()));
        approvals.recordPublishAttempt(approval.id(), requestId);
        approvals.appendEvent(ctx.tenantId(), approval, PUBLISH_STARTED, ctx.actorId(), null, requestId);
        audit.record(ctx, AUDIT_PUBLISH, locked.id(), "approval=" + approval.id()
                + " draftVersion=" + approval.draftVersion() + " request=" + requestId);
    }

    /** 草稿保存成功后调用（同一事务，UPDATE 已持问卷行锁）：任何未结申请即作废。 */
    void voidOnDraftSave(TenantContext ctx, UUID surveyId, int newDraftVersion) {
        approvals.lockOpen(surveyId).ifPresent(open ->
                voidRequest(ctx, open, "draft saved as version " + newDraftVersion));
    }

    /**
     * 发布成功落库时调用（发布收尾事务，持问卷行锁）。对所发布草稿版本的 approved 申请转为 published；
     * 其他未结申请（例如免审者直接发布时残留的待审申请）作废。
     */
    void onPublished(TenantContext ctx, UUID surveyId, int draftVersion, UUID requestId) {
        approvals.lockOpen(surveyId).ifPresent(open -> {
            if (open.status() == PublishApprovalStatus.APPROVED && open.draftVersion() == draftVersion) {
                PublishApprovalView published = approvals.transition(open.id(),
                        List.of(PublishApprovalStatus.APPROVED), PublishApprovalStatus.PUBLISHED, ctx.actorId(), null)
                        .orElseThrow();
                approvals.appendEvent(ctx.tenantId(), published, PUBLISHED, ctx.actorId(), null, requestId);
            } else {
                voidRequest(ctx, open, "survey published from draft version " + draftVersion);
            }
        });
    }

    private void voidRequest(TenantContext ctx, PublishApprovalView open, String reason) {
        PublishApprovalView voided = approvals.transition(open.id(),
                        List.of(PublishApprovalStatus.PENDING, PublishApprovalStatus.APPROVED),
                        PublishApprovalStatus.VOIDED, ctx.actorId(), reason)
                .orElseThrow();
        approvals.appendEvent(ctx.tenantId(), voided, VOIDED, ctx.actorId(), reason, null);
        audit.record(ctx, AUDIT_VOID, open.surveyId(), "approval=" + open.id() + " was=" + open.status().code());
    }
}
