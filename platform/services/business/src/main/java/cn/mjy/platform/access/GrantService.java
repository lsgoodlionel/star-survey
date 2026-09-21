package cn.mjy.platform.access;

import cn.mjy.platform.shared.TenantContext;
import cn.mjy.platform.shared.TenantId;
import cn.mjy.platform.shared.tenant.TenantScope;
import java.time.Clock;
import java.time.Duration;
import java.time.Instant;
import java.util.List;
import java.util.Set;
import java.util.UUID;
import org.springframework.security.access.AccessDeniedException;
import org.springframework.stereotype.Service;

/**
 * 授权的授予与撤销，全部服务端判定：
 * <ul>
 *   <li>操作者须在该范围上有"管理成员"权限；</li>
 *   <li>防提权：只能授出（或撤销）自己在该范围上已持有全部权限的角色；</li>
 *   <li>角色约束来自目录：仅整租户、限时上限、是否需要席位；</li>
 *   <li>租户至少保留一名在职所有者；</li>
 *   <li>每次变更与审计在同一事务内完成。</li>
 * </ul>
 */
@Service
public class GrantService {

    private final TenantScope tenantScope;
    private final AccessDecisionService access;
    private final RoleCatalogue catalogue;
    private final AccessResources resources;
    private final MemberRepository members;
    private final GrantRepository grants;
    private final TenantAccessLock lock;
    private final AccessAudit audit;
    private final Clock clock = Clock.systemUTC();

    GrantService(TenantScope tenantScope, AccessDecisionService access, RoleCatalogue catalogue,
            AccessResources resources, MemberRepository members, GrantRepository grants, TenantAccessLock lock,
            AccessAudit audit) {
        this.tenantScope = tenantScope;
        this.access = access;
        this.catalogue = catalogue;
        this.resources = resources;
        this.members = members;
        this.grants = grants;
        this.lock = lock;
        this.audit = audit;
    }

    /** 授予角色；同一成员、角色、范围已存在时更新到期时间并返回原 id。 */
    public UUID grant(TenantContext ctx, GrantRequest request) {
        TenantId tenant = ctx.tenantId();
        return tenantScope.call(tenant, () -> {
            lock.lock(tenant);
            RoleDefinition role = catalogue.find(request.roleCode())
                    .orElseThrow(() -> new IllegalArgumentException("unknown role: " + request.roleCode()));
            validateScope(tenant, role, request.resourceId());
            validateExpiry(role, request.expiresAt());
            validateGrantee(tenant, role, request.actorId());
            requireCanDelegate(ctx, role, request.resourceId());
            UUID id = grants.upsert(tenant, request, ctx.actorId());
            GrantView granted = grants.find(tenant, id).orElseThrow();
            audit.record(ctx, AccessAudit.GRANT_CREATE, AccessAudit.describeGrant(granted));
            return id;
        });
    }

    public void revoke(TenantContext ctx, UUID grantId) {
        TenantId tenant = ctx.tenantId();
        tenantScope.run(tenant, () -> {
            lock.lock(tenant);
            GrantView grant = grants.find(tenant, grantId)
                    .orElseThrow(() -> new IllegalArgumentException("unknown grant: " + grantId));
            revokeLocked(ctx, grant);
        });
    }

    /** 当前租户全部授权；需要租户级"管理成员"权限。 */
    public List<GrantView> list(TenantContext ctx) {
        return tenantScope.call(ctx.tenantId(), () -> {
            access.require(ctx, Permission.MANAGE_MEMBERS, null);
            return grants.list(ctx.tenantId());
        });
    }

    /** 撤销某成员的全部授权（移除成员时使用）。调用方须已在租户作用域内并持有租户成员锁。 */
    void revokeAllForLocked(TenantContext ctx, String actorId) {
        grants.listForActor(ctx.tenantId(), actorId).forEach(grant -> revokeLocked(ctx, grant));
    }

    private void revokeLocked(TenantContext ctx, GrantView grant) {
        RoleDefinition role = catalogue.find(grant.roleCode())
                .orElseThrow(() -> new IllegalStateException("grant refers to unknown role: " + grant.roleCode()));
        requireCanDelegate(ctx, role, grant.resourceId());
        if (isLastActiveOwner(ctx.tenantId(), grant)) {
            throw new IllegalStateException("cannot revoke the last active tenant owner");
        }
        grants.delete(ctx.tenantId(), grant.id());
        audit.record(ctx, AccessAudit.GRANT_REVOKE, AccessAudit.describeGrant(grant));
    }

    private boolean isLastActiveOwner(TenantId tenant, GrantView grant) {
        boolean ownerGrant = GrantRepository.OWNER_ROLE.equals(grant.roleCode()) && grant.resourceId() == null;
        boolean live = grant.expiresAt() == null || grant.expiresAt().isAfter(clock.instant());
        boolean activeMember = members.find(tenant, grant.actorId())
                .map(MemberRepository.Member::isActive).orElse(false);
        return ownerGrant && live && activeMember && grants.activeOwners(tenant) <= 1;
    }

    private void requireCanDelegate(TenantContext ctx, RoleDefinition role, UUID resourceId) {
        Set<Permission> held = access.held(ctx, resourceId);
        if (!held.contains(Permission.MANAGE_MEMBERS)) {
            throw new AccessDeniedException("manage-members denied on " + scope(resourceId));
        }
        if (!held.containsAll(role.permissions())) {
            throw new AccessDeniedException("cannot delegate role " + role.code() + " on " + scope(resourceId)
                    + ": it includes permissions the actor does not hold there");
        }
    }

    private void validateScope(TenantId tenant, RoleDefinition role, UUID resourceId) {
        if (resourceId == null) {
            return;
        }
        if (role.tenantWideOnly()) {
            throw new IllegalArgumentException("role " + role.code() + " can only be granted tenant-wide");
        }
        if (resources.kindOf(tenant, resourceId).isEmpty()) {
            throw new IllegalArgumentException("unknown resource: " + resourceId);
        }
    }

    private void validateExpiry(RoleDefinition role, Instant expiresAt) {
        Instant now = clock.instant();
        if (expiresAt != null && !expiresAt.isAfter(now)) {
            throw new IllegalArgumentException("expiresAt must be in the future");
        }
        if (role.maxGrantHours() == null) {
            return;
        }
        if (expiresAt == null) {
            throw new IllegalArgumentException("role " + role.code() + " must be granted with an expiry");
        }
        if (expiresAt.isAfter(now.plus(Duration.ofHours(role.maxGrantHours())))) {
            throw new IllegalArgumentException(
                    "role " + role.code() + " may be granted for at most " + role.maxGrantHours() + " hours");
        }
    }

    private void validateGrantee(TenantId tenant, RoleDefinition role, String actorId) {
        MemberRepository.Member member = members.find(tenant, actorId)
                .filter(m -> !m.isRemoved())
                .orElseThrow(() -> new IllegalArgumentException("not a member of this tenant: " + actorId));
        if (role.requiresSeat() && !member.usesSeat()) {
            throw new IllegalArgumentException("role " + role.code() + " requires a seat; " + actorId + " has none");
        }
    }

    private static String scope(UUID resourceId) {
        return resourceId == null ? "tenant" : resourceId.toString();
    }
}
