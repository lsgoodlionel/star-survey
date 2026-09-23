package cn.mjy.platform.delivery;

import cn.mjy.platform.audit.AuditLogRepository;
import cn.mjy.platform.shared.TenantContext;
import cn.mjy.platform.shared.TenantId;
import java.util.UUID;
import org.springframework.stereotype.Component;

/**
 * 投放相关的审计。与被审计的变更在同一事务里写入：变更回滚则审计也不留。
 *
 * <p>audit_log 没有明细列，内容编码在 resource 字段里。<b>绝不写入收件地址</b>——
 * 审计只记到收件人的内部标识（uuid），需要对应到人时由有权限的人去查收件人列表。
 */
@Component
class DeliveryAudit {

    static final String LINK_CREATE = "delivery.link.create";
    static final String LINK_REVOKE = "delivery.link.revoke";
    static final String TASK_CREATE = "delivery.task.create";
    static final String TASK_CANCEL = "delivery.task.cancel";
    static final String SEND = "delivery.send";
    static final String RECEIPT = "delivery.receipt";
    static final String UNSUBSCRIBE = "delivery.unsubscribe";
    static final String REMINDER_RULE = "delivery.reminder.rule";
    static final String REMINDER_SEND = "delivery.reminder.send";
    static final String NOTIFICATION_RULE = "delivery.notification.rule";
    static final String COMPLETION = "delivery.completion";

    private final AuditLogRepository auditLog;

    DeliveryAudit(AuditLogRepository auditLog) {
        this.auditLog = auditLog;
    }

    void record(TenantContext actor, String action, String resource) {
        auditLog.record(actor.tenantId(), actor.actorId(), action, resource, actor.traceId());
    }

    void record(TenantId tenant, String actorId, String traceId, String action, String resource) {
        auditLog.record(tenant, actorId, action, resource, traceId);
    }

    /** 一次发送的审计串：只有任务与收件人的内部标识、渠道与结果。 */
    static String describeSend(UUID taskId, UUID recipientId, DeliveryChannel channel, String outcome) {
        return "task/" + taskId + " recipient/" + recipientId + " channel=" + channel.code() + " outcome=" + outcome;
    }
}
