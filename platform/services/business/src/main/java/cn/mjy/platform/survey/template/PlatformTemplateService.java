package cn.mjy.platform.survey.template;

import cn.mjy.platform.shared.tenant.TenantScope;
import cn.mjy.platform.survey.SurveyDefinitions;
import java.util.List;
import java.util.UUID;
import org.springframework.stereotype.Service;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.node.ObjectNode;

/**
 * 平台（运营）模板库：运营提交定义、上架、下架。上架后所有租户都能在模板库里看到并复制。
 *
 * <p>运营模板的定义同样经 {@link TemplateDefinitions} 剥离：即便运营拿一份线上问卷的导出去建模板，
 * 参与者 token 与访问口令哈希也进不了库。
 *
 * <p>审核就是运营自己：一份运营模板只有在运营明确上架之后才对租户可见，
 * 这正是"对租户可见之前必须经过审核"要保证的事。
 *
 * <p>写操作都在控制平面租户的 {@link TenantScope} 里执行——两张表没有行级安全，
 * 但 audit_log 有，运营的动作要有地方落账。
 */
@Service
public class PlatformTemplateService {

    private final TenantScope tenantScope;
    private final PlatformTemplateRepository templates;
    private final TemplateDefinitions templateDefinitions;
    private final SurveyDefinitions definitions;
    private final TemplateAudit audit;

    PlatformTemplateService(TenantScope tenantScope, PlatformTemplateRepository templates,
            TemplateDefinitions templateDefinitions, SurveyDefinitions definitions, TemplateAudit audit) {
        this.tenantScope = tenantScope;
        this.templates = templates;
        this.templateDefinitions = templateDefinitions;
        this.definitions = definitions;
        this.audit = audit;
    }

    public TemplateView create(String operator, String traceId, String name, String description,
            JsonNode definition) {
        String cleanName = requireName(name);
        String stripped = strip(definition);
        UUID id = UUID.randomUUID();
        return tenantScope.call(TemplateAudit.CONTROL_PLANE, () -> {
            templates.insert(id, cleanName, trimmed(description), operator);
            templates.insertVersion(id, 1, stripped, operator);
            audit.recordOperator(operator, traceId, TemplateAudit.PLATFORM_CREATE, id, "version=1");
            return required(id);
        });
    }

    public TemplateView addVersion(String operator, String traceId, UUID templateId, JsonNode definition) {
        String stripped = strip(definition);
        return tenantScope.call(TemplateAudit.CONTROL_PLANE, () -> {
            templates.lock(templateId).orElseThrow(() -> notFound(templateId));
            int version = templates.nextVersion(templateId);
            templates.insertVersion(templateId, version, stripped, operator);
            audit.recordOperator(operator, traceId, TemplateAudit.PLATFORM_VERSION, templateId,
                    "version=" + version);
            return required(templateId);
        });
    }

    /** 上架某一版：之后所有租户都能复制它。 */
    public TemplateView publish(String operator, String traceId, UUID templateId, int versionNo) {
        if (versionNo <= 0) {
            throw new InvalidTemplateRequestException("versionNo must be positive");
        }
        return tenantScope.call(TemplateAudit.CONTROL_PLANE, () -> {
            templates.lock(templateId).orElseThrow(() -> notFound(templateId));
            TemplateView published = templates.publish(templateId, versionNo).orElseThrow(() ->
                    new TemplateConflictException("template_version_unknown",
                            "版本 " + versionNo + " 不存在或模板状态不允许上架"));
            audit.recordOperator(operator, traceId, TemplateAudit.PLATFORM_PUBLISH, templateId,
                    "version=" + versionNo);
            return published;
        });
    }

    /** 下架：之后不能再被复制；已经复制出去的问卷是租户自己的问卷，不受影响。 */
    public TemplateView unpublish(String operator, String traceId, UUID templateId) {
        return tenantScope.call(TemplateAudit.CONTROL_PLANE, () -> {
            templates.lock(templateId).orElseThrow(() -> notFound(templateId));
            TemplateView unpublished = templates.unpublish(templateId).orElseThrow(() ->
                    new TemplateConflictException("template_not_published", "只有已上架的模板可以下架"));
            audit.recordOperator(operator, traceId, TemplateAudit.PLATFORM_UNPUBLISH, templateId, "");
            return unpublished;
        });
    }

    /** 运营视角：所有运营模板，含未上架的。 */
    public List<TemplateView> list() {
        return tenantScope.call(TemplateAudit.CONTROL_PLANE, templates::all);
    }

    public TemplateDetail get(UUID templateId) {
        return tenantScope.call(TemplateAudit.CONTROL_PLANE, () -> new TemplateDetail(
                templates.find(templateId).orElseThrow(() -> notFound(templateId)),
                templates.versions(templateId), List.of()));
    }

    private String strip(JsonNode definition) {
        ObjectNode stripped = templateDefinitions.toTemplate(definition);
        return definitions.serialize(stripped);
    }

    private TemplateView required(UUID templateId) {
        return templates.find(templateId).orElseThrow(() -> notFound(templateId));
    }

    private static TemplateNotFoundException notFound(UUID templateId) {
        return new TemplateNotFoundException("platform template not found: " + templateId);
    }

    private static String requireName(String name) {
        String clean = trimmed(name);
        if (clean == null || clean.isEmpty()) {
            throw new InvalidTemplateRequestException("name is required");
        }
        if (clean.length() > SurveyTemplateService.MAX_NAME_LENGTH) {
            throw new InvalidTemplateRequestException(
                    "name is longer than " + SurveyTemplateService.MAX_NAME_LENGTH);
        }
        return clean;
    }

    private static String trimmed(String value) {
        return value == null ? null : value.strip();
    }
}
