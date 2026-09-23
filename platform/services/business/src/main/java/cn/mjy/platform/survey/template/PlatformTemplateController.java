package cn.mjy.platform.survey.template;

import cn.mjy.platform.tenant.PlatformOperatorGuard;
import jakarta.validation.Valid;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.NotNull;
import jakarta.validation.constraints.Positive;
import jakarta.validation.constraints.Size;
import java.util.List;
import java.util.UUID;
import org.springframework.http.HttpStatus;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.ResponseStatus;
import org.springframework.web.bind.annotation.RestController;
import tools.jackson.databind.JsonNode;

/**
 * 运营侧的模板库接口。整个 {@code /v1/platform/**} 由 {@code PlatformWebConfig} 的拦截器
 * 要求 {@code platform_operator} 角色；这里再调一次 guard 只是为了拿到调用者标识写审计。
 */
@RestController
@RequestMapping("/v1/platform/survey-templates")
class PlatformTemplateController {

    private final PlatformTemplateService templates;
    private final PlatformOperatorGuard guard;

    PlatformTemplateController(PlatformTemplateService templates, PlatformOperatorGuard guard) {
        this.templates = templates;
        this.guard = guard;
    }

    @PostMapping
    @ResponseStatus(HttpStatus.CREATED)
    TemplateView create(@Valid @RequestBody CreateTemplate request) {
        String operator = guard.requireOperator();
        return templates.create(operator, newTraceId(), request.name(), request.description(),
                request.definition());
    }

    @GetMapping
    List<TemplateView> list() {
        guard.requireOperator();
        return templates.list();
    }

    @GetMapping("/{templateId}")
    TemplateDetail get(@PathVariable UUID templateId) {
        guard.requireOperator();
        return templates.get(templateId);
    }

    @PostMapping("/{templateId}/versions")
    TemplateView addVersion(@PathVariable UUID templateId, @Valid @RequestBody AddVersion request) {
        String operator = guard.requireOperator();
        return templates.addVersion(operator, newTraceId(), templateId, request.definition());
    }

    @PostMapping("/{templateId}/publish")
    TemplateView publish(@PathVariable UUID templateId, @Valid @RequestBody Publish request) {
        String operator = guard.requireOperator();
        return templates.publish(operator, newTraceId(), templateId, request.versionNo());
    }

    @PostMapping("/{templateId}/unpublish")
    TemplateView unpublish(@PathVariable UUID templateId) {
        String operator = guard.requireOperator();
        return templates.unpublish(operator, newTraceId(), templateId);
    }

    private static String newTraceId() {
        return UUID.randomUUID().toString();
    }

    public record CreateTemplate(
            @NotBlank @Size(max = SurveyTemplateService.MAX_NAME_LENGTH) String name,
            @Size(max = 1000) String description,
            @NotNull JsonNode definition) {
    }

    public record AddVersion(@NotNull JsonNode definition) {
    }

    public record Publish(@NotNull @Positive Integer versionNo) {
    }
}
