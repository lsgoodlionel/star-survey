package cn.mjy.platform.survey;

import cn.mjy.platform.shared.security.CurrentTenant;
import jakarta.validation.Valid;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.NotNull;
import jakarta.validation.constraints.Positive;
import jakarta.validation.constraints.Size;
import java.util.List;
import java.util.UUID;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RestController;

/**
 * 发布审批接口。租户与操作者只取自已验签令牌；别的租户的问卷与申请一律 404。
 * <ul>
 *   <li>{@code POST /v1/surveys/{id}/approval-requests} 提交（需 publish）；</li>
 *   <li>{@code GET  /v1/surveys/{id}/approval-requests} 该问卷的全部申请（需 view）；</li>
 *   <li>{@code GET  /v1/publish-approvals/pending} 调用方可批准的待审申请；</li>
 *   <li>{@code GET  /v1/publish-approvals/{requestId}} 申请与决定历史（需 view）；</li>
 *   <li>{@code POST /v1/publish-approvals/{requestId}/approve | reject | withdraw}。</li>
 * </ul>
 */
@RestController
public class PublishApprovalController {

    private final PublishApprovalService approvals;
    private final CurrentTenant currentTenant;

    public PublishApprovalController(PublishApprovalService approvals, CurrentTenant currentTenant) {
        this.approvals = approvals;
        this.currentTenant = currentTenant;
    }

    public record Submit(@NotNull @Positive Integer draftVersion) {
    }

    public record Reject(@NotBlank @Size(max = PublishApprovalService.MAX_REASON_LENGTH) String reason) {
    }

    @PostMapping("/v1/surveys/{id}/approval-requests")
    public ResponseEntity<PublishApprovalView> submit(@PathVariable UUID id, @Valid @RequestBody Submit request) {
        return ResponseEntity.status(HttpStatus.CREATED)
                .body(approvals.submit(currentTenant.require(), id, request.draftVersion()));
    }

    @GetMapping("/v1/surveys/{id}/approval-requests")
    public List<PublishApprovalView> forSurvey(@PathVariable UUID id) {
        return approvals.forSurvey(currentTenant.require(), id);
    }

    @GetMapping("/v1/publish-approvals/pending")
    public List<PublishApprovalView> pending() {
        return approvals.pendingForApprover(currentTenant.require());
    }

    @GetMapping("/v1/publish-approvals/{requestId}")
    public PublishApprovalDetail get(@PathVariable UUID requestId) {
        return approvals.get(currentTenant.require(), requestId);
    }

    @PostMapping("/v1/publish-approvals/{requestId}/approve")
    public PublishApprovalView approve(@PathVariable UUID requestId) {
        return approvals.approve(currentTenant.require(), requestId);
    }

    @PostMapping("/v1/publish-approvals/{requestId}/reject")
    public PublishApprovalView reject(@PathVariable UUID requestId, @Valid @RequestBody Reject request) {
        return approvals.reject(currentTenant.require(), requestId, request.reason());
    }

    @PostMapping("/v1/publish-approvals/{requestId}/withdraw")
    public PublishApprovalView withdraw(@PathVariable UUID requestId) {
        return approvals.withdraw(currentTenant.require(), requestId);
    }
}
