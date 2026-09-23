package cn.mjy.platform.survey.template;

import cn.mjy.platform.audit.AuditLogRepository;
import cn.mjy.platform.shared.TenantContext;
import cn.mjy.platform.shared.TenantId;
import java.util.UUID;
import org.springframework.stereotype.Component;

/**
 * 模板库的审计。与业务变更在同一事务内写入：变更回滚则审计也不留。
 * audit_log 没有明细列，细节编码在 resource 字段里（"survey-template/&lt;id&gt; 键=值 …"）。
 */
@Component
class TemplateAudit {

    static final String CREATE = "survey.template.create";
    static final String VERSION = "survey.template.version";
    static final String SUBMIT = "survey.template.submit";
    static final String APPROVE = "survey.template.approve";
    static final String REJECT = "survey.template.reject";
    static final String UNPUBLISH = "survey.template.unpublish";
    static final String COPY = "survey.template.copy";
    static final String PLATFORM_CREATE = "platform.template.create";
    static final String PLATFORM_VERSION = "platform.template.version";
    static final String PLATFORM_PUBLISH = "platform.template.publish";
    static final String PLATFORM_UNPUBLISH = "platform.template.unpublish";

    /** 运营操作落账用的控制平面租户：不是任何真实租户，真实租户的审计里看不到它。 */
    static final TenantId CONTROL_PLANE = new TenantId(new UUID(0, 0));

    private final AuditLogRepository auditLog;

    TemplateAudit(AuditLogRepository auditLog) {
        this.auditLog = auditLog;
    }

    void record(TenantContext actor, String action, UUID templateId, String details) {
        auditLog.record(actor.tenantId(), actor.actorId(), action, resource(templateId, details),
                actor.traceId());
    }

    /**
     * 运营操作没有租户上下文：记在控制平面租户（全零 UUID）名下，与任何真实租户的审计分开。
     * audit_log 有行级安全，所以调用方必须已经在 {@link #CONTROL_PLANE} 的 TenantScope 里。
     */
    void recordOperator(String operator, String traceId, String action, UUID templateId, String details) {
        auditLog.record(CONTROL_PLANE, operator, action,
                "platform-survey-template/" + templateId + suffix(details), traceId);
    }

    private static String resource(UUID templateId, String details) {
        return "survey-template/" + templateId + suffix(details);
    }

    private static String suffix(String details) {
        return details == null || details.isEmpty() ? "" : " " + details;
    }
}
