package cn.mjy.platform.access;

import cn.mjy.platform.shared.TenantContext;
import cn.mjy.platform.shared.TenantId;
import cn.mjy.platform.shared.tenant.TenantScope;
import java.util.Optional;
import org.springframework.stereotype.Service;

/**
 * 成员与席位。邀请即占席位（员工类），接受邀请后授权才生效；移除成员会撤销其全部授权并释放席位。
 * 会改变席位占用的操作都在租户成员锁内完成，并发邀请最后一个席位时恰好一个成功。
 */
@Service
public class MemberService {

    static final String SYSTEM_ACTOR = "system";

    private final TenantScope tenantScope;
    private final AccessDecisionService access;
    private final MemberRepository members;
    private final GrantRepository grants;
    private final GrantService grantService;
    private final SeatGuard seats;
    private final TenantAccessLock lock;
    private final AccessAudit audit;

    MemberService(TenantScope tenantScope, AccessDecisionService access, MemberRepository members,
            GrantRepository grants, GrantService grantService, SeatGuard seats, TenantAccessLock lock,
            AccessAudit audit) {
        this.tenantScope = tenantScope;
        this.access = access;
        this.members = members;
        this.grants = grants;
        this.grantService = grantService;
        this.seats = seats;
        this.lock = lock;
        this.audit = audit;
    }

    /**
     * 为新开通的租户设立首位所有者。只供租户开通流程调用，且只在租户还没有任何成员时成功，
     * 因此不能被用来给已有租户"补"一个所有者。
     */
    public void bootstrapOwner(TenantId tenant, String ownerActorId, String traceId) {
        requireActorId(ownerActorId);
        tenantScope.run(tenant, () -> {
            lock.lock(tenant);
            if (members.hasAnyMember(tenant)) {
                throw new IllegalStateException("tenant already has members; owner bootstrap is only for new tenants");
            }
            seats.requireFreeSeat(tenant, 0);
            members.upsert(tenant, ownerActorId, MemberRepository.ACTIVE, true, SYSTEM_ACTOR);
            GrantRequest ownerGrant = new GrantRequest(ownerActorId, GrantRepository.OWNER_ROLE, null, null);
            GrantView granted = grants.find(tenant, grants.upsert(tenant, ownerGrant, SYSTEM_ACTOR)).orElseThrow();
            audit.record(tenant, SYSTEM_ACTOR, traceId, AccessAudit.OWNER_BOOTSTRAP, AccessAudit.describeGrant(granted));
        });
    }

    /** 邀请成员；需要租户级"管理成员"权限。已是成员（未移除）时幂等返回，不再占席位。 */
    public void invite(TenantContext ctx, String inviteeActorId, MemberKind kind) {
        requireActorId(inviteeActorId);
        TenantId tenant = ctx.tenantId();
        tenantScope.run(tenant, () -> {
            access.require(ctx, Permission.MANAGE_MEMBERS, null);
            lock.lock(tenant);
            Optional<MemberRepository.Member> existing = members.find(tenant, inviteeActorId);
            if (existing.isPresent() && !existing.get().isRemoved()) {
                return;
            }
            if (kind.usesSeat()) {
                seats.requireFreeSeat(tenant, members.seatsInUse(tenant));
            }
            members.upsert(tenant, inviteeActorId, MemberRepository.INVITED, kind.usesSeat(), ctx.actorId());
            audit.record(ctx, AccessAudit.MEMBER_INVITE, "member/" + inviteeActorId + " kind=" + kind);
        });
    }

    /** 被邀请人本人接受邀请。 */
    public void acceptInvitation(TenantContext ctx) {
        tenantScope.run(ctx.tenantId(), () -> {
            int changed = members.changeStatus(ctx.tenantId(), ctx.actorId(), MemberRepository.INVITED,
                    MemberRepository.ACTIVE);
            if (changed == 0) {
                throw new IllegalStateException("no pending invitation for " + ctx.actorId());
            }
            audit.record(ctx, AccessAudit.MEMBER_ACCEPT, "member/" + ctx.actorId());
        });
    }

    /** 移除成员：撤销其全部授权（逐条审计、逐条防提权校验）并释放席位。不能移除最后一名所有者。 */
    public void remove(TenantContext ctx, String actorId) {
        TenantId tenant = ctx.tenantId();
        tenantScope.run(tenant, () -> {
            access.require(ctx, Permission.MANAGE_MEMBERS, null);
            lock.lock(tenant);
            members.find(tenant, actorId).filter(m -> !m.isRemoved())
                    .orElseThrow(() -> new IllegalArgumentException("not a member of this tenant: " + actorId));
            grantService.revokeAllForLocked(ctx, actorId);
            members.markRemoved(tenant, actorId);
            audit.record(ctx, AccessAudit.MEMBER_REMOVE, "member/" + actorId);
        });
    }

    /** 当前占用的席位数（含邀请中）；需要租户级"管理成员"权限。 */
    public long seatsInUse(TenantContext ctx) {
        return tenantScope.call(ctx.tenantId(), () -> {
            access.require(ctx, Permission.MANAGE_MEMBERS, null);
            return members.seatsInUse(ctx.tenantId());
        });
    }

    private static void requireActorId(String actorId) {
        if (actorId == null || actorId.isBlank()) {
            throw new IllegalArgumentException("actorId is required");
        }
    }
}
