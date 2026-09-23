package cn.mjy.platform.contacts;

import cn.mjy.platform.shared.TenantContext;
import cn.mjy.platform.shared.tenant.TenantScope;
import java.time.Instant;
import java.util.List;
import java.util.UUID;
import org.springframework.stereotype.Service;

/**
 * 部门级数据范围的授予与撤销（ADR 0017 决定 4）。
 * 只有租户管理员（租户级 manage-settings）能改范围——部门管理员无法自己扩大范围。
 * 范围行只收窄，永不放宽：没有范围行的成员看不到任何联系人。
 */
@Service
public class ContactScopeService {

    private final TenantScope tenantScope;
    private final ContactAccess access;
    private final ContactScopeRepository scopes;
    private final OrgUnitService units;
    private final ContactsAudit audit;

    ContactScopeService(TenantScope tenantScope, ContactAccess access, ContactScopeRepository scopes,
            OrgUnitService units, ContactsAudit audit) {
        this.tenantScope = tenantScope;
        this.access = access;
        this.scopes = scopes;
        this.units = units;
        this.audit = audit;
    }

    public ContactScopeView grant(TenantContext ctx, String actorId, UUID orgUnitId, Instant expiresAt) {
        String actor = DedupeKey.trim(actorId);
        if (actor == null) {
            throw new InvalidContactRequestException("actorId is required");
        }
        if (expiresAt != null && !expiresAt.isAfter(Instant.now())) {
            throw new InvalidContactRequestException("expiresAt must be in the future");
        }
        return tenantScope.call(ctx.tenantId(), () -> {
            access.requireTenantAdmin(ctx);
            units.requireVisible(ContactScope.wholeTenant(ctx.actorId()), orgUnitId);
            ContactScopeView granted = scopes.upsert(UUID.randomUUID(), actor, orgUnitId, expiresAt,
                    ctx.actorId());
            audit.record(ctx, ContactsAudit.SCOPE_GRANT, "contact-scope", granted.id(),
                    actor + " on org-unit/" + orgUnitId);
            return granted;
        });
    }

    public void revoke(TenantContext ctx, UUID scopeId) {
        tenantScope.run(ctx.tenantId(), () -> {
            access.requireTenantAdmin(ctx);
            ContactScopeView grant = scopes.find(scopeId)
                    .orElseThrow(() -> new ContactNotFoundException("contact scope not found: " + scopeId));
            scopes.delete(scopeId);
            audit.record(ctx, ContactsAudit.SCOPE_REVOKE, "contact-scope", scopeId, grant.actorId());
        });
    }

    public List<ContactScopeView> list(TenantContext ctx) {
        return tenantScope.call(ctx.tenantId(), () -> {
            access.requireTenantAdmin(ctx);
            return scopes.list();
        });
    }

    /** 离职即失权：撤掉此人的全部部门范围。已在租户作用域内时由停用联系人的路径调用。 */
    int revokeAllFor(TenantContext ctx, String actorId) {
        int removed = scopes.deleteAllFor(actorId);
        if (removed > 0) {
            audit.record(ctx, ContactsAudit.SCOPE_REVOKE, "actor/" + actorId + " all(" + removed + ")");
        }
        return removed;
    }
}
