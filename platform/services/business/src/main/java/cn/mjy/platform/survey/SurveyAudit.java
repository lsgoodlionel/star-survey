package cn.mjy.platform.survey;

import cn.mjy.platform.audit.AuditLogRepository;
import cn.mjy.platform.shared.TenantContext;
import java.util.UUID;
import org.springframework.stereotype.Component;

/**
 * 问卷模块的审计。与业务变更在同一事务内写入：变更回滚则审计也不留。
 * audit_log 没有明细列，细节编码在 resource 字段里（"survey/<id> 键=值 …"）。
 */
@Component
class SurveyAudit {

    static final String CREATE = "survey.create";
    static final String DRAFT_SAVE = "survey.draft.save";
    static final String DRAFT_RESTORE = "survey.draft.restore";
    static final String DRAFT_IMPORT = "survey.draft.import";
    static final String PUBLISH = "survey.publish";
    static final String PUBLISH_FAILED = "survey.publish.failed";
    static final String PUBLISH_PENDING = "survey.publish.pending";
    static final String ROUTE_SWITCH = "survey.publish.route_switch";

    private final AuditLogRepository auditLog;

    SurveyAudit(AuditLogRepository auditLog) {
        this.auditLog = auditLog;
    }

    void record(TenantContext actor, String action, UUID surveyId, String details) {
        String resource = "survey/" + surveyId + (details.isEmpty() ? "" : " " + details);
        auditLog.record(actor.tenantId(), actor.actorId(), action, resource, actor.traceId());
    }
}
