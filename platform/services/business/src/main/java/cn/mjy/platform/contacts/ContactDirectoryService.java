package cn.mjy.platform.contacts;

import cn.mjy.platform.contacts.ContactNormalizer.Normalized;
import cn.mjy.platform.contacts.ContactRepository.ContactRow;
import cn.mjy.platform.contacts.ContactRepository.NewContact;
import cn.mjy.platform.shared.TenantContext;
import cn.mjy.platform.shared.tenant.TenantScope;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Objects;
import java.util.Optional;
import java.util.UUID;
import org.springframework.stereotype.Service;

/**
 * 联系人目录（WP-18 切片 18.1/18.2）。三类身份各自成行、永不隐式合并：
 * 去重只在同一名单内按该名单的去重键进行，目录联系人按 (类别, provider, app_id, external_id) 认人。
 * 跨类、跨名单的"同一个人"只能用 {@link #link} 显式关联，两行都保留。
 */
@Service
public class ContactDirectoryService {

    static final int DEFAULT_PAGE_SIZE = 50;
    static final int MAX_PAGE_SIZE = 200;
    private static final String SOURCE_MANUAL = "manual";
    private static final int MAX_LINK_REASON = 200;

    private final TenantScope tenantScope;
    private final ContactAccess access;
    private final ContactRepository contacts;
    private final ContactListService lists;
    private final OrgUnitService units;
    private final ContactLinkRepository links;
    private final ContactScopeService scopes;
    private final ContactNormalizer normalizer;
    private final ContactsAudit audit;

    ContactDirectoryService(TenantScope tenantScope, ContactAccess access, ContactRepository contacts,
            ContactListService lists, OrgUnitService units, ContactLinkRepository links,
            ContactScopeService scopes, ContactNormalizer normalizer, ContactsAudit audit) {
        this.tenantScope = tenantScope;
        this.access = access;
        this.contacts = contacts;
        this.lists = lists;
        this.units = units;
        this.links = links;
        this.scopes = scopes;
        this.normalizer = normalizer;
        this.audit = audit;
    }

    /** 新建或按去重键命中已有联系人（命中则合并更新）。listId 为空表示不属于任何名单的目录联系人。 */
    public ContactView create(TenantContext ctx, UUID listId, ContactKind kind, ContactDraft draft) {
        Objects.requireNonNull(ctx, "ctx");
        if (kind == null) {
            throw new InvalidContactRequestException("kind is required");
        }
        return tenantScope.call(ctx.tenantId(), () -> {
            ContactScope scope = access.scopeOf(ctx);
            try {
                Upsert upsert = upsert(ctx, scope, listId, kind, draft, SOURCE_MANUAL);
                return view(upsert.row());
            } catch (ContactRowRejectedException e) {
                throw new InvalidContactRequestException(e.reason());
            }
        });
    }

    public ContactView get(TenantContext ctx, UUID contactId) {
        return tenantScope.call(ctx.tenantId(), () -> view(requireVisible(access.scopeOf(ctx), contactId)));
    }

    public ContactView update(TenantContext ctx, UUID contactId, ContactDraft draft) {
        return tenantScope.call(ctx.tenantId(), () -> {
            ContactScope scope = access.scopeOf(ctx);
            ContactRow current = requireVisible(scope, contactId);
            DedupeKey key = current.listId() == null ? null : lists.require(current.listId()).dedupeKey();
            try {
                Normalized normalized = normalizer.normalize(null, draft);
                if (normalized.draft().orgUnitId() != null) {
                    units.requireVisible(scope, normalized.draft().orgUnitId());
                }
                String dedupeValue = key == null ? null : recomputeDedupe(key, current, normalized.draft());
                ContactRow updated = contacts.update(contactId, normalized.draft(), dedupeValue);
                audit.record(ctx, ContactsAudit.CONTACT_UPDATE, "contact", contactId);
                return view(updated);
            } catch (ContactRowRejectedException e) {
                throw new InvalidContactRequestException(e.reason());
            }
        });
    }

    /** 停用联系人（离职、退订）。员工联系人同时收回它的部门范围——离职即失权。 */
    public ContactView disable(TenantContext ctx, UUID contactId) {
        return tenantScope.call(ctx.tenantId(), () -> {
            ContactScope scope = access.scopeOf(ctx);
            ContactRow current = requireVisible(scope, contactId);
            contacts.setStatus(contactId, "disabled");
            if (current.actorId() != null) {
                scopes.revokeAllFor(ctx, current.actorId());
            }
            audit.record(ctx, ContactsAudit.CONTACT_DISABLE, "contact", contactId);
            return view(requireVisible(scope, contactId));
        });
    }

    public ContactPage search(TenantContext ctx, ContactQuery query) {
        ContactQuery effective = query == null ? ContactQuery.all() : query;
        int limit = pageSize(effective.limit());
        return tenantScope.call(ctx.tenantId(), () -> {
            ContactScope scope = access.scopeOf(ctx);
            if (effective.listId() != null) {
                lists.require(effective.listId());
            }
            if (effective.orgUnitId() != null) {
                units.requireVisible(scope, effective.orgUnitId());
            }
            List<ContactRow> rows = contacts.search(scope, effective, limit + 1);
            boolean more = rows.size() > limit;
            List<ContactRow> page = more ? rows.subList(0, limit) : rows;
            return new ContactPage(views(page), more ? page.getLast().id().toString() : null);
        });
    }

    /** 显式关联两个联系人（不是合并）：两行都保留自己的 id、名单、部门与标签。 */
    public ContactLinkView link(TenantContext ctx, UUID first, UUID second, String reason) {
        String trimmed = DedupeKey.trim(reason);
        if (first == null || second == null || first.equals(second)) {
            throw new InvalidContactRequestException("two different contacts are required");
        }
        if (trimmed == null || trimmed.length() > MAX_LINK_REASON) {
            throw new InvalidContactRequestException("reason must be 1..200 characters");
        }
        // 按 uuid 的文本序规范化：PostgreSQL 的 uuid 比较是按字节的，等同规范文本的字典序，
        // 而 Java 的 UUID.compareTo 按有符号 long 比较，两者结论可能相反（会顶到表上的 CHECK）。
        boolean firstIsLeft = first.toString().compareTo(second.toString()) < 0;
        UUID left = firstIsLeft ? first : second;
        UUID right = firstIsLeft ? second : first;
        return tenantScope.call(ctx.tenantId(), () -> {
            ContactScope scope = access.scopeOf(ctx);
            requireVisible(scope, left);
            requireVisible(scope, right);
            Optional<ContactLinkView> existing = links.findOpen(left, right);
            if (existing.isPresent()) {
                return existing.get();
            }
            ContactLinkView created = links.insert(UUID.randomUUID(), left, right, trimmed, ctx.actorId());
            audit.record(ctx, ContactsAudit.LINK_CREATE, "contact-link", created.id(),
                    left + "+" + right);
            return created;
        });
    }

    public void unlink(TenantContext ctx, UUID linkId) {
        tenantScope.run(ctx.tenantId(), () -> {
            ContactScope scope = access.scopeOf(ctx);
            ContactLinkView link = links.find(linkId)
                    .orElseThrow(() -> new ContactNotFoundException("contact link not found: " + linkId));
            requireVisible(scope, link.leftContactId());
            requireVisible(scope, link.rightContactId());
            if (links.revoke(linkId, ctx.actorId())) {
                audit.record(ctx, ContactsAudit.LINK_REVOKE, "contact-link", linkId);
            }
        });
    }

    public List<ContactLinkView> links(TenantContext ctx, UUID contactId) {
        return tenantScope.call(ctx.tenantId(), () -> {
            ContactScope scope = access.scopeOf(ctx);
            requireVisible(scope, contactId);
            return links.openFor(contactId);
        });
    }

    /** 导入用：已在租户作用域内、已判过权限，返回"这一行是新建还是更新"。 */
    Upsert importRow(TenantContext ctx, ContactScope scope, UUID listId, ContactKind kind, ContactDraft draft) {
        return upsert(ctx, scope, listId, kind, draft, "import");
    }

    /** 一次写入的结果：命中的行，以及它是新建（true）还是更新（false）。 */
    record Upsert(ContactRow row, boolean created) {
    }

    private Upsert upsert(TenantContext ctx, ContactScope scope, UUID listId, ContactKind kind,
            ContactDraft draft, String source) {
        DedupeKey key = null;
        if (listId != null) {
            ContactListView list = lists.require(listId);
            if (list.kind() != kind) {
                throw new InvalidContactRequestException(
                        "contact kind " + kind.code() + " does not match list kind " + list.kind().code());
            }
            key = list.dedupeKey();
        }
        Normalized normalized = normalizer.normalize(key, draft);
        ContactDraft clean = normalized.draft();
        requireKindOwnership(kind, clean);
        requireUnitWritable(scope, clean.orgUnitId());
        if (listId == null && clean.identity() == null) {
            throw new ContactRowRejectedException("missing_external_id");
        }

        Optional<ContactRow> existing = listId == null
                ? contacts.findDirectoryByIdentity(kind, clean.identity())
                : contacts.findInListByDedupe(listId, normalized.dedupeValue());
        if (existing.isPresent()) {
            ContactRow updated = contacts.update(existing.get().id(), clean, normalized.dedupeValue());
            audit.record(ctx, ContactsAudit.CONTACT_UPDATE, "contact", updated.id());
            return new Upsert(updated, false);
        }
        String dedupeValue = listId == null ? clean.identity().dedupeValue() : normalized.dedupeValue();
        ContactRow created = contacts.insert(
                new NewContact(UUID.randomUUID(), kind, listId, clean, dedupeValue, source, ctx.actorId()));
        audit.record(ctx, ContactsAudit.CONTACT_CREATE, "contact", created.id(), kind.code());
        return new Upsert(created, true);
    }

    /** 更新时若改动了去重键对应的字段，去重值同步重算；否则保持不变。 */
    private String recomputeDedupe(DedupeKey key, ContactRow current, ContactDraft draft) {
        ContactDraft merged = new ContactDraft(
                draft.displayName(), or(draft.email(), current.email()), or(draft.phone(), current.phone()),
                or(draft.employeeId(), current.employeeId()),
                draft.identity() == null ? current.identity() : draft.identity(),
                draft.orgUnitId(), draft.actorId(), draft.principalId());
        return normalizer.normalize(key, merged).dedupeValue();
    }

    private void requireKindOwnership(ContactKind kind, ContactDraft draft) {
        if (draft.actorId() != null && kind != ContactKind.STAFF) {
            throw new ContactRowRejectedException("actor_only_on_staff");
        }
        if (draft.principalId() != null && kind != ContactKind.ORG_MEMBER) {
            throw new ContactRowRejectedException("principal_only_on_org_member");
        }
    }

    /** 部门管理员只能往自己的部门里写；全租户管理员可以不指定部门。 */
    private void requireUnitWritable(ContactScope scope, UUID orgUnitId) {
        if (orgUnitId != null) {
            units.requireVisible(scope, orgUnitId);
            return;
        }
        if (!scope.wholeTenant()) {
            throw new ContactRowRejectedException("org_unit_required");
        }
    }

    private ContactRow requireVisible(ContactScope scope, UUID contactId) {
        return contacts.findVisible(scope, contactId)
                .orElseThrow(() -> new ContactNotFoundException("contact not found: " + contactId));
    }

    private ContactView view(ContactRow row) {
        return toView(row, contacts.tagsOf(row.id()));
    }

    private List<ContactView> views(List<ContactRow> rows) {
        Map<UUID, List<String>> tags = new HashMap<>();
        for (ContactRepository.TagRow tag : contacts.tagsOf(rows.stream().map(ContactRow::id).toList())) {
            tags.computeIfAbsent(tag.contactId(), id -> new ArrayList<>()).add(tag.name());
        }
        Map<UUID, ContactView> ordered = new LinkedHashMap<>();
        for (ContactRow row : rows) {
            ordered.put(row.id(), toView(row, tags.getOrDefault(row.id(), List.of())));
        }
        return List.copyOf(ordered.values());
    }

    private static ContactView toView(ContactRow row, List<String> tags) {
        return new ContactView(row.id(), row.kind(), row.listId(), row.orgUnitId(), row.displayName(),
                row.email(), row.phone(), row.employeeId(), row.identity(), row.source(), row.status(),
                tags, row.createdAt(), row.updatedAt());
    }

    private static String or(String candidate, String fallback) {
        return candidate == null ? fallback : candidate;
    }

    private static int pageSize(Integer requested) {
        if (requested == null) {
            return DEFAULT_PAGE_SIZE;
        }
        if (requested < 1 || requested > MAX_PAGE_SIZE) {
            throw new InvalidContactRequestException("limit must be 1.." + MAX_PAGE_SIZE);
        }
        return requested;
    }
}
