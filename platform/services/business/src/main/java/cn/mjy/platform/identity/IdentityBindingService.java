package cn.mjy.platform.identity;

import cn.mjy.platform.access.AccessDecisionService;
import cn.mjy.platform.access.Permission;
import cn.mjy.platform.audit.AuditLogRepository;
import cn.mjy.platform.shared.TenantContext;
import cn.mjy.platform.shared.TenantId;
import cn.mjy.platform.shared.tenant.TenantScope;
import cn.mjy.platform.tenant.api.CreateResult;
import java.util.Optional;
import java.util.UUID;
import org.springframework.stereotype.Service;

/**
 * 外部身份绑定（最小模型）。同一外部身份在每个租户内只绑定一次，重复绑定幂等返回；
 * 不同租户各自绑定、互不相关（见 V104），因此绑定结果不会透露此人是否存在于别的租户。
 *
 * <p>两类入口：
 * <ul>
 *   <li>带 {@link TenantContext} 的方法供租户端 API 使用，做权限判定：建绑定、按身份反查、读别人的绑定
 *       都需要租户级 manage-members；读自己的绑定不需要——"自己"指令牌主体（sub）与主体标识的规范字符串
 *       逐字相等（组织免登签发的令牌 sub 即主体标识，且每个请求另有会话撤销检查）。判定先于查询，
 *       无权者对存在与不存在的主体一律 403，不给出存在性线索；跨租户仍由行级安全表现为 404。</li>
 *   <li>只带租户标识的方法供系统流程（组织免登、同步）使用，不做权限判定，调用方负责。</li>
 * </ul>
 */
@Service
public class IdentityBindingService {

    private static final String ACTION_BIND = "identity.bind";

    private final TenantScope tenantScope;
    private final IdentityBindingRepository bindings;
    private final AuditLogRepository audit;
    private final AccessDecisionService access;

    public IdentityBindingService(TenantScope tenantScope, IdentityBindingRepository bindings,
                                  AuditLogRepository audit, AccessDecisionService access) {
        this.tenantScope = tenantScope;
        this.bindings = bindings;
        this.audit = audit;
        this.access = access;
    }

    /** 管理员建绑定；需要租户级 manage-members。 */
    public CreateResult<IdentityBinding> bind(TenantContext ctx, ExternalIdentity identity) {
        access.require(ctx, Permission.MANAGE_MEMBERS, null);
        return bind(ctx.tenantId(), identity, ctx.actorId(), ctx.traceId());
    }

    /** 按身份反查主体；需要租户级 manage-members（反查即枚举，不对本人开放）。 */
    public Optional<IdentityBinding> findByIdentity(TenantContext ctx, ExternalIdentity identity) {
        access.require(ctx, Permission.MANAGE_MEMBERS, null);
        return findByIdentity(ctx.tenantId(), identity);
    }

    /** 读某主体的绑定：本人可读自己的；读别人的需要租户级 manage-members。 */
    public Optional<IdentityBinding> findByPrincipal(TenantContext ctx, UUID principalId) {
        if (!isSelf(ctx, principalId)) {
            access.require(ctx, Permission.MANAGE_MEMBERS, null);
        }
        return findByPrincipal(ctx.tenantId(), principalId);
    }

    /** 令牌主体就是该主体：sub 与主体标识的规范（小写）字符串逐字相等。 */
    static boolean isSelf(TenantContext ctx, UUID principalId) {
        return principalId.toString().equals(ctx.actorId());
    }

    public CreateResult<IdentityBinding> bind(TenantId tenant, ExternalIdentity identity, String actorId, String traceId) {
        return tenantScope.call(tenant, () -> {
            Optional<IdentityBinding> inserted = bindings.insertIfAbsent(tenant, identity);
            if (inserted.isPresent()) {
                IdentityBinding binding = inserted.get();
                audit.record(tenant, actorId, ACTION_BIND, "principal/" + binding.principalId(), traceId);
                return new CreateResult<>(binding, true);
            }
            // 唯一键含租户，冲突只可能来自本租户的既有绑定，因此一定可见。
            return bindings.findByIdentity(identity)
                    .map(existing -> new CreateResult<>(existing, false))
                    .orElseThrow(() -> new IllegalStateException("conflicting binding is not visible in its own tenant"));
        });
    }

    public Optional<IdentityBinding> findByIdentity(TenantId tenant, ExternalIdentity identity) {
        return tenantScope.call(tenant, () -> bindings.findByIdentity(identity));
    }

    public Optional<IdentityBinding> findByPrincipal(TenantId tenant, UUID principalId) {
        return tenantScope.call(tenant, () -> bindings.findByPrincipal(principalId));
    }
}
