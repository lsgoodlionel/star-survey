package cn.mjy.platform.contacts;

import cn.mjy.platform.shared.TenantContext;
import cn.mjy.platform.shared.tenant.TenantScope;
import java.util.ArrayList;
import java.util.List;
import java.util.Objects;
import java.util.UUID;
import org.springframework.dao.DuplicateKeyException;
import org.springframework.stereotype.Service;

/**
 * 部门树（WP-18 切片 18.2）。建根部门与跨部门移动属于租户管理员；
 * 部门管理员只能在自己的范围内动作——移动要求新旧父节点都在范围内，否则等于把人搬出 / 搬进别的部门。
 */
@Service
public class OrgUnitService {

    private static final int MAX_NAME = 200;

    private final TenantScope tenantScope;
    private final ContactAccess access;
    private final OrgUnitRepository units;
    private final ContactsAudit audit;

    OrgUnitService(TenantScope tenantScope, ContactAccess access, OrgUnitRepository units, ContactsAudit audit) {
        this.tenantScope = tenantScope;
        this.access = access;
        this.units = units;
        this.audit = audit;
    }

    public OrgUnitView create(TenantContext ctx, UUID parentId, String name, String externalRef) {
        Objects.requireNonNull(ctx, "ctx");
        String trimmed = requireName(name);
        return tenantScope.call(ctx.tenantId(), () -> {
            ContactScope scope = access.scopeOf(ctx);
            if (parentId == null) {
                access.requireTenantAdmin(ctx);
            } else {
                requireVisible(scope, parentId);
            }
            OrgUnitView created;
            try {
                created = units.insert(UUID.randomUUID(), parentId, trimmed, DedupeKey.trim(externalRef),
                        ctx.actorId());
            } catch (DuplicateKeyException e) {
                throw new ContactConflictException("unit_name_taken", "a sibling unit is already named " + trimmed);
            }
            audit.record(ctx, ContactsAudit.UNIT_CREATE, "org-unit", created.id());
            return created;
        });
    }

    public List<OrgUnitView> list(TenantContext ctx) {
        return tenantScope.call(ctx.tenantId(), () -> units.list(access.scopeOf(ctx)));
    }

    public OrgUnitView get(TenantContext ctx, UUID unitId) {
        return tenantScope.call(ctx.tenantId(), () -> {
            ContactScope scope = access.scopeOf(ctx);
            requireVisible(scope, unitId);
            return units.find(unitId).orElseThrow(() -> notFound(unitId));
        });
    }

    public OrgUnitView rename(TenantContext ctx, UUID unitId, String name) {
        String trimmed = requireName(name);
        return tenantScope.call(ctx.tenantId(), () -> {
            ContactScope scope = access.scopeOf(ctx);
            requireVisible(scope, unitId);
            try {
                units.rename(unitId, trimmed);
            } catch (DuplicateKeyException e) {
                throw new ContactConflictException("unit_name_taken", "a sibling unit is already named " + trimmed);
            }
            audit.record(ctx, ContactsAudit.UNIT_RENAME, "org-unit", unitId);
            return units.find(unitId).orElseThrow(() -> notFound(unitId));
        });
    }

    /** 移动部门：新旧位置都要在调用者范围内；不能挂到自己的子孙下。 */
    public OrgUnitView move(TenantContext ctx, UUID unitId, UUID newParentId) {
        if (unitId == null) {
            throw new InvalidContactRequestException("unitId is required");
        }
        if (unitId.equals(newParentId)) {
            throw new InvalidContactRequestException("a unit cannot be its own parent");
        }
        return tenantScope.call(ctx.tenantId(), () -> {
            ContactScope scope = access.scopeOf(ctx);
            requireVisible(scope, unitId);
            OrgUnitView current = units.find(unitId).orElseThrow(() -> notFound(unitId));
            if (newParentId == null) {
                access.requireTenantAdmin(ctx);
            } else {
                requireVisible(scope, newParentId);
            }
            if (current.parentId() != null) {
                requireVisible(scope, current.parentId());
            } else {
                access.requireTenantAdmin(ctx);
            }
            lockTree(unitId, newParentId);
            if (newParentId != null && units.isDescendant(newParentId, unitId)) {
                throw new ContactConflictException("move_into_descendant",
                        "cannot move " + unitId + " under its own descendant");
            }
            units.reparent(unitId, newParentId);
            audit.record(ctx, ContactsAudit.UNIT_MOVE, "org-unit", unitId,
                    "to " + (newParentId == null ? "root" : newParentId.toString()));
            return units.find(unitId).orElseThrow(() -> notFound(unitId));
        });
    }

    /** 名单导入按部门号找部门：找不到就是这一行不合法，不是整批失败。 */
    OrgUnitView requireByExternalRef(ContactScope scope, String externalRef) {
        OrgUnitView unit = units.findByExternalRef(externalRef)
                .orElseThrow(() -> new ContactRowRejectedException("unknown_org_unit"));
        if (!units.isVisible(scope, unit.id())) {
            throw new ContactRowRejectedException("unknown_org_unit");
        }
        return unit;
    }

    void requireVisible(ContactScope scope, UUID unitId) {
        if (unitId == null || !units.isVisible(scope, unitId)) {
            throw notFound(unitId);
        }
    }

    private void lockTree(UUID unitId, UUID newParentId) {
        List<UUID> ids = new ArrayList<>();
        ids.add(unitId);
        if (newParentId != null) {
            ids.add(newParentId);
        }
        units.lock(ids);
    }

    private static String requireName(String name) {
        String trimmed = DedupeKey.trim(name);
        if (trimmed == null || trimmed.length() > MAX_NAME) {
            throw new InvalidContactRequestException("name must be 1..200 characters");
        }
        return trimmed;
    }

    private static ContactNotFoundException notFound(UUID unitId) {
        return new ContactNotFoundException("org unit not found: " + unitId);
    }
}
