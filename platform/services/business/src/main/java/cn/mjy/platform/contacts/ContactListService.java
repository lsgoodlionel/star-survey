package cn.mjy.platform.contacts;

import cn.mjy.platform.shared.TenantContext;
import cn.mjy.platform.shared.tenant.TenantScope;
import java.util.List;
import java.util.Objects;
import java.util.UUID;
import org.springframework.dao.DuplicateKeyException;
import org.springframework.stereotype.Service;

/**
 * 联系人名单：建名单时由租户选定身份类别与去重键，之后都不可改
 * （换键等于重新划分已有行的去重分组，只能新建名单再导入）。
 */
@Service
public class ContactListService {

    private static final int MAX_NAME = 200;

    private final TenantScope tenantScope;
    private final ContactAccess access;
    private final ContactListRepository lists;
    private final ContactsAudit audit;

    ContactListService(TenantScope tenantScope, ContactAccess access, ContactListRepository lists,
            ContactsAudit audit) {
        this.tenantScope = tenantScope;
        this.access = access;
        this.lists = lists;
        this.audit = audit;
    }

    public ContactListView create(TenantContext ctx, String name, ContactKind kind, DedupeKey dedupeKey) {
        Objects.requireNonNull(ctx, "ctx");
        String trimmed = DedupeKey.trim(name);
        if (trimmed == null || trimmed.length() > MAX_NAME) {
            throw new InvalidContactRequestException("name must be 1..200 characters");
        }
        if (kind == null || dedupeKey == null) {
            throw new InvalidContactRequestException("kind and dedupeKey are required");
        }
        return tenantScope.call(ctx.tenantId(), () -> {
            access.requireTenantAdmin(ctx);
            ContactListView created;
            try {
                created = lists.insert(UUID.randomUUID(), trimmed, kind, dedupeKey, ctx.actorId());
            } catch (DuplicateKeyException e) {
                throw new ContactConflictException("list_name_taken", "a list named " + trimmed + " already exists");
            }
            audit.record(ctx, ContactsAudit.LIST_CREATE, "contact-list", created.id(),
                    kind.code() + "/" + dedupeKey.code());
            return created;
        });
    }

    public List<ContactListView> list(TenantContext ctx) {
        return tenantScope.call(ctx.tenantId(), () -> {
            access.scopeOf(ctx);
            return lists.list();
        });
    }

    public ContactListView get(TenantContext ctx, UUID listId) {
        return tenantScope.call(ctx.tenantId(), () -> {
            access.scopeOf(ctx);
            return require(listId);
        });
    }

    /** 已在租户作用域内时的内部读取：不存在等同不可见（404）。 */
    ContactListView require(UUID listId) {
        return lists.find(listId)
                .orElseThrow(() -> new ContactNotFoundException("contact list not found: " + listId));
    }
}
