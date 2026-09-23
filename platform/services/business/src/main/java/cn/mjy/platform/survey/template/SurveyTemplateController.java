package cn.mjy.platform.survey.template;

import cn.mjy.platform.shared.TenantContext;
import cn.mjy.platform.shared.security.CurrentTenant;
import cn.mjy.platform.survey.SurveyView;
import jakarta.validation.Valid;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.NotNull;
import jakarta.validation.constraints.Size;
import java.util.List;
import java.util.UUID;
import org.springframework.http.HttpStatus;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.ResponseStatus;
import org.springframework.web.bind.annotation.RestController;
import tools.jackson.databind.JsonNode;

/** 租户侧的模板库接口。租户与调用者一律取自令牌，路径与请求体里的 id 只用来定位资源。 */
@RestController
@RequestMapping("/v1/survey-templates")
class SurveyTemplateController {

    private final SurveyTemplateService templates;
    private final CurrentTenant currentTenant;

    SurveyTemplateController(SurveyTemplateService templates, CurrentTenant currentTenant) {
        this.templates = templates;
        this.currentTenant = currentTenant;
    }

    @PostMapping
    @ResponseStatus(HttpStatus.CREATED)
    TemplateView create(@Valid @RequestBody CreateTemplate request) {
        TenantContext ctx = currentTenant.require();
        return templates.createFromSurvey(ctx, request.sourceSurveyId(), request.name(), request.description());
    }

    @GetMapping
    List<TemplateView> list(@RequestParam(required = false) String scope) {
        return templates.list(currentTenant.require(), TemplateScope.fromCode(scope));
    }

    /** 审核队列。字面量段比 {@code /{templateId}} 更具体，不会被它吃掉。 */
    @GetMapping("/pending")
    List<TemplateView> pending() {
        return templates.pendingForReview(currentTenant.require());
    }

    @GetMapping("/{templateId}")
    TemplateDetail get(@PathVariable UUID templateId) {
        return templates.get(currentTenant.require(), templateId);
    }

    @GetMapping("/{templateId}/versions/{versionNo}")
    JsonNode definition(@PathVariable UUID templateId, @PathVariable int versionNo) {
        return templates.definition(currentTenant.require(), templateId, versionNo);
    }

    @PostMapping("/{templateId}/versions")
    TemplateView addVersion(@PathVariable UUID templateId, @Valid @RequestBody AddVersion request) {
        return templates.addVersionFromSurvey(currentTenant.require(), templateId, request.sourceSurveyId());
    }

    @PostMapping("/{templateId}/submit")
    TemplateView submit(@PathVariable UUID templateId) {
        return templates.submitForReview(currentTenant.require(), templateId);
    }

    @PostMapping("/{templateId}/approve")
    TemplateView approve(@PathVariable UUID templateId) {
        return templates.approve(currentTenant.require(), templateId);
    }

    @PostMapping("/{templateId}/reject")
    TemplateView reject(@PathVariable UUID templateId, @Valid @RequestBody Reject request) {
        return templates.reject(currentTenant.require(), templateId, request.reason());
    }

    @PostMapping("/{templateId}/unpublish")
    TemplateView unpublish(@PathVariable UUID templateId,
            @RequestBody(required = false) Unpublish request) {
        return templates.unpublish(currentTenant.require(), templateId,
                request == null ? null : request.reason());
    }

    @PostMapping("/{templateId}/copies")
    @ResponseStatus(HttpStatus.CREATED)
    SurveyView copy(@PathVariable UUID templateId, @Valid @RequestBody Copy request) {
        return templates.copyToSurvey(currentTenant.require(), templateId, request.parentId(), request.title());
    }

    public record CreateTemplate(
            @NotNull UUID sourceSurveyId,
            @NotBlank @Size(max = SurveyTemplateService.MAX_NAME_LENGTH) String name,
            @Size(max = 1000) String description) {
    }

    public record AddVersion(@NotNull UUID sourceSurveyId) {
    }

    public record Reject(@NotBlank @Size(max = SurveyTemplateService.MAX_REASON_LENGTH) String reason) {
    }

    public record Unpublish(@Size(max = SurveyTemplateService.MAX_REASON_LENGTH) String reason) {
    }

    public record Copy(@NotNull UUID parentId, @Size(max = 500) String title) {
    }
}
