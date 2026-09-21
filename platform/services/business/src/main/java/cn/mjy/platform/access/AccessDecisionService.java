package cn.mjy.platform.access;

import cn.mjy.platform.shared.TenantContext;
import cn.mjy.platform.shared.TenantId;
import cn.mjy.platform.shared.tenant.TenantScope;
import java.util.List;
import java.util.Objects;
import java.util.Optional;
import java.util.Set;
import java.util.UUID;
import java.util.stream.Collectors;
import org.springframework.security.access.AccessDeniedException;
import org.springframework.stereotype.Service;

/**
 * 租户内授权判定：{@code can(上下文, 动作, 资源)}。
 *
 * <p>规则（全部由数据驱动，没有按角色名分支）：
 * <ol>
 *   <li>资源须存在于当前租户（行级安全下别的租户的资源等同不存在）；</li>
 *   <li>操作者须是本租户在职成员（令牌里的 roles 声明不参与判定）；</li>
 *   <li>存在一条未过期授权，范围是整个租户或资源本身及其任一祖先，且其角色含该权限；</li>
 *   <li>发布另受租户审核开关约束：开关打开且没有"免审发布"权限时拒绝，原因 APPROVAL_REQUIRED。</li>
 * </ol>
 * 多条授权同时命中时，按"离资源最近、再按角色码"选出一条作为原因，结果确定。
 */
@Service
public class AccessDecisionService {

    private final TenantScope tenantScope;
    private final AccessResources resources;
    private final MemberRepository members;
    private final GrantRepository grants;
    private final AccessSettingsRepository settings;

    public AccessDecisionService(TenantScope tenantScope, AccessResources resources, MemberRepository members,
            GrantRepository grants, AccessSettingsRepository settings) {
        this.tenantScope = tenantScope;
        this.resources = resources;
        this.members = members;
        this.grants = grants;
        this.settings = settings;
    }

    /** 判定能否对资源执行动作；resourceId 为空表示租户级动作（如管理设置、账单）。 */
    public Decision can(TenantContext ctx, Permission action, UUID resourceId) {
        Objects.requireNonNull(ctx, "ctx");
        Objects.requireNonNull(action, "action");
        return tenantScope.call(ctx.tenantId(), () -> decide(ctx, action, resourceId));
    }

    public Decision canInTenant(TenantContext ctx, Permission action) {
        return can(ctx, action, null);
    }

    /** 判定不通过时抛出 {@link AccessDeniedException}，消息包含原因。 */
    public void require(TenantContext ctx, Permission action, UUID resourceId) {
        Decision decision = can(ctx, action, resourceId);
        if (!decision.allowed()) {
            throw new AccessDeniedException(action.code() + " denied: " + decision.reason() + " " + decision.detail());
        }
    }

    /**
     * 操作者在该范围上实际持有的全部权限（不考虑发布审核开关）；非在职成员或资源未知时为空集。
     * 用于"不能授出自己没有的权限"的防提权校验。
     */
    Set<Permission> held(TenantContext ctx, UUID resourceId) {
        return tenantScope.call(ctx.tenantId(), () -> {
            TenantId tenant = ctx.tenantId();
            boolean known = resourceId == null || resources.kindOf(tenant, resourceId).isPresent();
            boolean active = members.find(tenant, ctx.actorId()).map(MemberRepository.Member::isActive).orElse(false);
            if (!known || !active) {
                return Set.<Permission>of();
            }
            return grants.matches(tenant, ctx.actorId(), resourceId).stream()
                    .map(GrantMatch::permission)
                    .collect(Collectors.toUnmodifiableSet());
        });
    }

    private Decision decide(TenantContext ctx, Permission action, UUID resourceId) {
        TenantId tenant = ctx.tenantId();
        if (resourceId != null && resources.kindOf(tenant, resourceId).isEmpty()) {
            return Decision.deny(DecisionReason.UNKNOWN_RESOURCE, "resource " + resourceId);
        }
        boolean active = members.find(tenant, ctx.actorId()).map(MemberRepository.Member::isActive).orElse(false);
        if (!active) {
            return Decision.deny(DecisionReason.NOT_AN_ACTIVE_MEMBER, "actor " + ctx.actorId());
        }
        List<GrantMatch> matches = grants.matches(tenant, ctx.actorId(), resourceId);
        Optional<GrantMatch> match = GrantMatch.closest(matches, action);
        if (match.isEmpty()) {
            return Decision.deny(DecisionReason.NO_MATCHING_GRANT, "no grant includes " + action.code());
        }
        if (action == Permission.PUBLISH && needsApproval(tenant, matches)) {
            return Decision.deny(DecisionReason.APPROVAL_REQUIRED,
                    "tenant requires publish approval; " + match.get().describe() + " cannot bypass it");
        }
        return Decision.allow(match.get().describe());
    }

    private boolean needsApproval(TenantId tenant, List<GrantMatch> matches) {
        boolean canBypass = GrantMatch.closest(matches, Permission.PUBLISH_WITHOUT_APPROVAL).isPresent();
        return !canBypass && settings.publishApprovalRequired(tenant);
    }
}
