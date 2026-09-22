package cn.mjy.platform.survey;

import cn.mjy.platform.shared.TenantContext;
import cn.mjy.platform.shared.security.CurrentTenant;
import jakarta.validation.Valid;
import jakarta.validation.constraints.NotNull;
import jakarta.validation.constraints.Positive;
import java.util.List;
import java.util.UUID;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.PutMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;
import tools.jackson.databind.JsonNode;

/**
 * 租户端问卷接口。租户与操作者只取自已验签令牌。
 *
 * <p>发布的 HTTP 状态：published 200；publish_failed 按网关结局 422（定义被拒）或 502（引擎失败 / 网关拒收）；
 * pending_reconciliation 202（结果未知，再次调用即以同一 requestId 核对）。
 */
@RestController
@RequestMapping("/v1/surveys")
public class SurveyController {

    private static final int REJECTED_DEFINITION = 422;

    private final SurveyService surveys;
    private final SurveyPublishService publisher;
    private final CurrentTenant currentTenant;

    public SurveyController(SurveyService surveys, SurveyPublishService publisher, CurrentTenant currentTenant) {
        this.surveys = surveys;
        this.publisher = publisher;
        this.currentTenant = currentTenant;
    }

    public record CreateSurvey(@NotNull UUID parentId, @NotNull JsonNode definition) {
    }

    public record SaveDraft(@NotNull @Positive Integer expectedVersion, @NotNull JsonNode definition) {
    }

    @PostMapping
    public ResponseEntity<SurveyView> create(@Valid @RequestBody CreateSurvey request) {
        TenantContext ctx = currentTenant.require();
        return ResponseEntity.status(HttpStatus.CREATED)
                .body(surveys.create(ctx, request.parentId(), request.definition()));
    }

    @GetMapping("/{id}")
    public SurveyView get(@PathVariable UUID id) {
        return surveys.get(currentTenant.require(), id);
    }

    @GetMapping("/{id}/draft")
    public DraftView draft(@PathVariable UUID id) {
        return surveys.draft(currentTenant.require(), id);
    }

    @PutMapping("/{id}/draft")
    public DraftView saveDraft(@PathVariable UUID id, @Valid @RequestBody SaveDraft request) {
        return surveys.saveDraft(currentTenant.require(), id, request.expectedVersion(), request.definition());
    }

    @PostMapping("/{id}/publish")
    public ResponseEntity<PublishOutcome> publish(@PathVariable UUID id) {
        PublishOutcome outcome = publisher.publish(currentTenant.require(), id);
        return ResponseEntity.status(statusOf(outcome.survey())).body(outcome);
    }

    @GetMapping("/{id}/versions")
    public List<PublishedVersionView> versions(@PathVariable UUID id) {
        return surveys.versions(currentTenant.require(), id);
    }

    @GetMapping("/{id}/versions/{version}")
    public PublishedVersionView version(@PathVariable UUID id, @PathVariable int version) {
        return surveys.version(currentTenant.require(), id, version);
    }

    private static HttpStatus statusOf(SurveyView survey) {
        return switch (survey.status()) {
            case PUBLISHED -> HttpStatus.OK;
            case PUBLISH_FAILED -> isRejectedDefinition(survey) ? HttpStatus.UNPROCESSABLE_CONTENT : HttpStatus.BAD_GATEWAY;
            case PENDING_RECONCILIATION, PUBLISHING, DRAFT -> HttpStatus.ACCEPTED;
        };
    }

    private static boolean isRejectedDefinition(SurveyView survey) {
        return survey.lastPublish() != null && survey.lastPublish().gatewayStatus() != null
                && survey.lastPublish().gatewayStatus() == REJECTED_DEFINITION;
    }
}
