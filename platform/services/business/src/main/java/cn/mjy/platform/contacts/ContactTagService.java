package cn.mjy.platform.contacts;

import cn.mjy.platform.shared.TenantContext;
import cn.mjy.platform.shared.tenant.TenantScope;
import java.util.List;
import java.util.UUID;
import org.springframework.stereotype.Service;

/** 标签（WP-18 切片 18.2）：每租户一套；打标签要能看到那个联系人，跨部门、跨租户一律 404。 */
@Service
public class ContactTagService {

    private static final int MAX_NAME = 64;

    private final TenantScope tenantScope;
    private final ContactAccess access;
    private final ContactTagRepository tags;
    private final ContactRepository contacts;
    private final ContactsAudit audit;

    ContactTagService(TenantScope tenantScope, ContactAccess access, ContactTagRepository tags,
            ContactRepository contacts, ContactsAudit audit) {
        this.tenantScope = tenantScope;
        this.access = access;
        this.tags = tags;
        this.contacts = contacts;
        this.audit = audit;
    }

    /** 建标签；同名（不分大小写）已存在时返回原标签。 */
    public ContactTagView create(TenantContext ctx, String name) {
        String trimmed = DedupeKey.trim(name);
        if (trimmed == null || trimmed.length() > MAX_NAME) {
            throw new InvalidContactRequestException("name must be 1..64 characters");
        }
        return tenantScope.call(ctx.tenantId(), () -> {
            access.scopeOf(ctx);
            return tags.findByName(trimmed).orElseGet(() -> {
                ContactTagView created = tags.insert(UUID.randomUUID(), trimmed, ctx.actorId());
                audit.record(ctx, ContactsAudit.TAG_CREATE, "contact-tag", created.id());
                return created;
            });
        });
    }

    public List<ContactTagView> list(TenantContext ctx) {
        return tenantScope.call(ctx.tenantId(), () -> {
            access.scopeOf(ctx);
            return tags.list();
        });
    }

    public void assign(TenantContext ctx, UUID contactId, UUID tagId) {
        tenantScope.run(ctx.tenantId(), () -> {
            ContactScope scope = access.scopeOf(ctx);
            requireVisible(scope, contactId);
            requireTag(tagId);
            tags.assign(contactId, tagId, ctx.actorId());
            audit.record(ctx, ContactsAudit.TAG_ASSIGN, "contact", contactId, "tag/" + tagId);
        });
    }

    public void unassign(TenantContext ctx, UUID contactId, UUID tagId) {
        tenantScope.run(ctx.tenantId(), () -> {
            ContactScope scope = access.scopeOf(ctx);
            requireVisible(scope, contactId);
            requireTag(tagId);
            tags.unassign(contactId, tagId);
            audit.record(ctx, ContactsAudit.TAG_UNASSIGN, "contact", contactId, "tag/" + tagId);
        });
    }

    private void requireVisible(ContactScope scope, UUID contactId) {
        if (contacts.findVisible(scope, contactId).isEmpty()) {
            throw new ContactNotFoundException("contact not found: " + contactId);
        }
    }

    private void requireTag(UUID tagId) {
        if (tags.find(tagId).isEmpty()) {
            throw new ContactNotFoundException("contact tag not found: " + tagId);
        }
    }
}
