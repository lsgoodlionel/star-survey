package cn.mjy.platform.access;

import cn.mjy.platform.audit.AuditLogRepository;
import cn.mjy.platform.shared.TenantContext;
import cn.mjy.platform.shared.TenantId;
import org.springframework.stereotype.Component;

/**
 * 权限变更的审计。与变更在同一事务内写入：变更回滚则审计也不留，变更成功则审计必在。
 * audit_log 没有明细列，变更内容编码在 resource 字段里（键=值，空格分隔）。
 */
@Component
class AccessAudit {

    static final String GRANT_CREATE = "access.grant.create";
    static final String GRANT_REVOKE = "access.grant.revoke";
    static final String MEMBER_INVITE = "access.member.invite";
    static final String MEMBER_ACCEPT = "access.member.accept";
    static final String MEMBER_REMOVE = "access.member.remove";
    static final String OWNER_BOOTSTRAP = "access.owner.bootstrap";
    static final String SETTINGS_PUBLISH_APPROVAL = "access.settings.publish_approval";
    static final String RESOURCE_CREATE = "access.resource.create";
    static final String RESOURCE_RENAME = "access.resource.rename";
    static final String RESOURCE_MOVE = "access.resource.move";

    private final AuditLogRepository auditLog;

    AccessAudit(AuditLogRepository auditLog) {
        this.auditLog = auditLog;
    }

    void record(TenantContext actor, String action, String resource) {
        auditLog.record(actor.tenantId(), actor.actorId(), action, resource, actor.traceId());
    }

    void record(TenantId tenant, String actorId, String traceId, String action, String resource) {
        auditLog.record(tenant, actorId, action, resource, traceId);
    }

    static String describeGrant(GrantView grant) {
        return "grant/" + grant.id() + " actor=" + grant.actorId() + " role=" + grant.roleCode()
                + " scope=" + grant.scopeLabel() + " expires=" + (grant.expiresAt() == null ? "never" : grant.expiresAt());
    }
}
