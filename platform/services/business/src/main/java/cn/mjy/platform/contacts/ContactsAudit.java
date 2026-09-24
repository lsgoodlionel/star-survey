package cn.mjy.platform.contacts;

import cn.mjy.platform.audit.AuditLogRepository;
import cn.mjy.platform.shared.TenantContext;
import java.util.UUID;
import org.springframework.stereotype.Component;

/**
 * 通讯录的审计写入。资源串里只放 UUID 与动作码，<b>绝不</b>放姓名、邮箱、手机号或令牌
 * （ADR 0017 决定 6）。须与业务写入在同一个租户事务里调用，回滚时审计一并回滚。
 */
@Component
class ContactsAudit {

    static final String LIST_CREATE = "contacts.list.create";
    static final String UNIT_CREATE = "contacts.unit.create";
    static final String UNIT_RENAME = "contacts.unit.rename";
    static final String UNIT_MOVE = "contacts.unit.move";
    static final String CONTACT_CREATE = "contacts.contact.create";
    static final String CONTACT_UPDATE = "contacts.contact.update";
    static final String CONTACT_DISABLE = "contacts.contact.disable";
    static final String LINK_CREATE = "contacts.link.create";
    static final String LINK_REVOKE = "contacts.link.revoke";
    static final String TAG_CREATE = "contacts.tag.create";
    static final String TAG_ASSIGN = "contacts.tag.assign";
    static final String TAG_UNASSIGN = "contacts.tag.unassign";
    static final String SCOPE_GRANT = "contacts.scope.grant";
    static final String SCOPE_REVOKE = "contacts.scope.revoke";
    static final String IMPORT_SUBMIT = "contacts.import.submit";
    static final String IMPORT_FINISH = "contacts.import.finish";
    static final String PARTICIPATION_LINK = "contacts.participation.link";
    static final String PARTICIPATION_REVOKE = "contacts.participation.revoke";
    static final String AUDIENCE_SET = "contacts.audience.set";
    static final String AUDIENCE_CLEAR = "contacts.audience.clear";

    private final AuditLogRepository audit;

    ContactsAudit(AuditLogRepository audit) {
        this.audit = audit;
    }

    void record(TenantContext ctx, String action, String resource) {
        audit.record(ctx.tenantId(), ctx.actorId(), action, resource, ctx.traceId());
    }

    void record(TenantContext ctx, String action, String prefix, UUID id) {
        record(ctx, action, prefix + "/" + id);
    }

    void record(TenantContext ctx, String action, String prefix, UUID id, String detail) {
        record(ctx, action, prefix + "/" + id + " " + detail);
    }
}
