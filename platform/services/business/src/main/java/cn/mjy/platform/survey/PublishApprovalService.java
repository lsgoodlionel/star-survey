package cn.mjy.platform.survey;

import cn.mjy.platform.access.AccessDecisionService;
import cn.mjy.platform.access.Permission;
import cn.mjy.platform.shared.TenantContext;
import cn.mjy.platform.shared.tenant.TenantScope;
import cn.mjy.platform.survey.SurveyRepository.SurveyRow;
import java.util.List;
import java.util.Objects;
import java.util.UUID;
import org.springframework.stereotype.Service;

/**
 * 发布审批流程（R01-09，ADR 0011）：提交、批准、驳回、撤回与查询。
 *
 * <ul>
 *   <li>提交：须持有问卷的 publish（无论是否免审）；绑定当前草稿版本；同一问卷同一时刻只有一条未结申请；</li>
 *   <li>批准 / 驳回：须对问卷或其上级拥有 approve-publish；申请人本人拥有该权限时可以自批；驳回须给原因；</li>
 *   <li>撤回：只有申请人本人；pending 与 approved 都可撤回。</li>
 * </ul>
 * 每个改状态的操作都先取问卷行锁、再锁申请行（与改稿、发布相同的加锁顺序），并以"从哪个状态迁出"为条件更新，
 * 因此与并发的改稿作废不会交错。每一步都写审计与只追加的决定历史，与状态变更同一事务。
 * 权限判定一律经 access 模块；租户只取自调用方上下文，别的租户的申请在行级安全下等同不存在（404）。
 */
@Service
public class PublishApprovalService {

    static final String APPROVAL_ALREADY_OPEN = "approval_already_open";
    static final String APPROVAL_NOT_PENDING = "approval_not_pending";
    static final String APPROVAL_NOT_OPEN = "approval_not_open";

    static final String AUDIT_SUBMIT = "survey.approval.submit";
    static final String AUDIT_APPROVE = "survey.approval.approve";
    static final String AUDIT_REJECT = "survey.approval.reject";
    static final String AUDIT_WITHDRAW = "survey.approval.withdraw";

    static final int MAX_REASON_LENGTH = 1000;

    private static final List<PublishApprovalStatus> PENDING_ONLY = List.of(PublishApprovalStatus.PENDING);
    private static final List<PublishApprovalStatus> OPEN =
            List.of(PublishApprovalStatus.PENDING, PublishApprovalStatus.APPROVED);

    private final TenantScope tenantScope;
    private final SurveyAccess access;
    private final AccessDecisionService decisions;
    private final SurveyRepository surveys;
    private final PublishApprovalRepository approvals;
    private final SurveyAudit audit;

    PublishApprovalService(TenantScope tenantScope, SurveyAccess access, AccessDecisionService decisions,
            SurveyRepository surveys, PublishApprovalRepository approvals, SurveyAudit audit) {
        this.tenantScope = tenantScope;
        this.access = access;
        this.decisions = decisions;
        this.surveys = surveys;
        this.approvals = approvals;
        this.audit = audit;
    }

    /** 为当前草稿版本提交发布申请；draftVersion 须等于当前草稿版本（申请人看到的就是被审的）。 */
    public PublishApprovalView submit(TenantContext ctx, UUID surveyId, int draftVersion) {
        Objects.requireNonNull(ctx, "ctx");
        return tenantScope.call(ctx.tenantId(), () -> {
            access.requirePublishRight(ctx, surveyId);
            SurveyRow survey = lockSurvey(surveyId);
            requireSubmittable(survey);
            if (survey.draftVersion() != draftVersion) {
                throw new SurveyConflictException(SurveyConflictException.VERSION_CONFLICT,
                        "draft version is " + survey.draftVersion() + ", request names " + draftVersion);
            }
            approvals.lockOpen(surveyId).ifPresent(open -> {
                throw new SurveyConflictException(APPROVAL_ALREADY_OPEN,
                        "survey already has an open approval request " + open.id());
            });
            UUID id = UUID.randomUUID();
            approvals.insertPending(ctx.tenantId(), id, surveyId, draftVersion, ctx.actorId());
            PublishApprovalView submitted = approvals.find(id).orElseThrow();
            record(ctx, submitted, "submitted", AUDIT_SUBMIT, null);
            return submitted;
        });
    }

    public PublishApprovalView approve(TenantContext ctx, UUID approvalId) {
        return decide(ctx, approvalId, PublishApprovalStatus.APPROVED, null);
    }

    public PublishApprovalView reject(TenantContext ctx, UUID approvalId, String reason) {
        return decide(ctx, approvalId, PublishApprovalStatus.REJECTED, requireReason(reason));
    }

    /** 申请人撤回自己的未结申请（待审或已批准未发布）。 */
    public PublishApprovalView withdraw(TenantContext ctx, UUID approvalId) {
        Objects.requireNonNull(ctx, "ctx");
        return tenantScope.call(ctx.tenantId(), () -> {
            PublishApprovalView request = requireRequest(approvalId);
            access.require(ctx, Permission.VIEW, request.surveyId());
            if (!request.applicant().equals(ctx.actorId())) {
                throw new PublishApprovalForbiddenException(PublishApprovalForbiddenException.NOT_APPLICANT,
                        "only the applicant may withdraw approval request " + approvalId);
            }
            lockSurvey(request.surveyId());
            PublishApprovalView withdrawn = approvals.transition(approvalId, OPEN, PublishApprovalStatus.WITHDRAWN,
                            ctx.actorId(), null)
                    .orElseThrow(() -> new SurveyConflictException(APPROVAL_NOT_OPEN,
                            "approval request " + approvalId + " is no longer open"));
            record(ctx, withdrawn, "withdrawn", AUDIT_WITHDRAW, null);
            return withdrawn;
        });
    }

    /** 调用方可以批准的待审申请（本租户内，逐条按 approve-publish 判定），按提交时间排序。 */
    public List<PublishApprovalView> pendingForApprover(TenantContext ctx) {
        Objects.requireNonNull(ctx, "ctx");
        return tenantScope.call(ctx.tenantId(), () -> approvals.pending().stream()
                .filter(request -> decisions.can(ctx, Permission.APPROVE_PUBLISH, request.surveyId()).allowed())
                .toList());
    }

    public List<PublishApprovalView> forSurvey(TenantContext ctx, UUID surveyId) {
        Objects.requireNonNull(ctx, "ctx");
        return tenantScope.call(ctx.tenantId(), () -> {
            access.require(ctx, Permission.VIEW, surveyId);
            return approvals.forSurvey(surveyId);
        });
    }

    public PublishApprovalDetail get(TenantContext ctx, UUID approvalId) {
        Objects.requireNonNull(ctx, "ctx");
        return tenantScope.call(ctx.tenantId(), () -> {
            PublishApprovalView request = requireRequest(approvalId);
            access.require(ctx, Permission.VIEW, request.surveyId());
            return new PublishApprovalDetail(request, approvals.history(approvalId));
        });
    }

    /** 批准或驳回：只作用于待审申请；批准时再核一次绑定的草稿版本就是当前版本（改稿会在同一锁下先作废申请）。 */
    private PublishApprovalView decide(TenantContext ctx, UUID approvalId, PublishApprovalStatus outcome,
            String reason) {
        Objects.requireNonNull(ctx, "ctx");
        return tenantScope.call(ctx.tenantId(), () -> {
            PublishApprovalView request = requireRequest(approvalId);
            access.require(ctx, Permission.APPROVE_PUBLISH, request.surveyId());
            SurveyRow survey = lockSurvey(request.surveyId());
            PublishApprovalView decided = approvals.transition(approvalId, PENDING_ONLY, outcome, ctx.actorId(),
                            reason)
                    .orElseThrow(() -> new SurveyConflictException(APPROVAL_NOT_PENDING,
                            "approval request " + approvalId + " is no longer pending"));
            if (decided.draftVersion() != survey.draftVersion()) {
                // 不应发生：改稿与作废同一事务且持同一把锁。万一发生，整笔回滚，绝不留下对旧版本的批准。
                throw new SurveyConflictException(SurveyConflictException.VERSION_CONFLICT,
                        "request is for draft version " + decided.draftVersion() + ", draft is "
                                + survey.draftVersion());
            }
            boolean approved = outcome == PublishApprovalStatus.APPROVED;
            record(ctx, decided, approved ? "approved" : "rejected", approved ? AUDIT_APPROVE : AUDIT_REJECT, reason);
            return decided;
        });
    }

    private void record(TenantContext ctx, PublishApprovalView request, String event, String auditAction,
            String reason) {
        approvals.appendEvent(ctx.tenantId(), request, event, ctx.actorId(), reason, null);
        audit.record(ctx, auditAction, request.surveyId(), "approval=" + request.id()
                + " draftVersion=" + request.draftVersion() + " applicant=" + request.applicant());
    }

    private PublishApprovalView requireRequest(UUID approvalId) {
        return approvals.find(approvalId).orElseThrow(() ->
                new SurveyNotFoundException("approval request not found: " + approvalId));
    }

    private SurveyRow lockSurvey(UUID surveyId) {
        return surveys.lock(surveyId, SurveyPublishService.STALE_PUBLISHING)
                .orElseThrow(() -> new SurveyNotFoundException("survey not found: " + surveyId));
    }

    /**
     * 没有发布在途、且草稿不是当前在线版本的问卷可以提交申请——已上线的问卷改稿之后照样要走审批
     * 才能重新发布（ADR 0012）。
     */
    private static void requireSubmittable(SurveyRow survey) {
        if (survey.liveVersionIsCurrentDraft()) {
            throw new SurveyConflictException(SurveyConflictException.ALREADY_PUBLISHED,
                    "draft version " + survey.draftVersion() + " is already published");
        }
        switch (survey.status()) {
            case DRAFT, PUBLISH_FAILED, PUBLISHED -> {
            }
            case PUBLISHING, PENDING_RECONCILIATION -> throw new SurveyConflictException(
                    SurveyConflictException.PUBLISH_IN_PROGRESS, "a publish of this survey is still unresolved");
        }
    }

    private static String requireReason(String reason) {
        String trimmed = reason == null ? "" : reason.strip();
        if (trimmed.isEmpty()) {
            throw new InvalidSurveyRequestException("reason is required when rejecting");
        }
        if (trimmed.length() > MAX_REASON_LENGTH) {
            throw new InvalidSurveyRequestException("reason must be at most " + MAX_REASON_LENGTH + " characters");
        }
        return trimmed;
    }
}
