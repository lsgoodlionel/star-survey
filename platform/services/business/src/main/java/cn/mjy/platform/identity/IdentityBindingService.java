package cn.mjy.platform.identity;

import cn.mjy.platform.audit.AuditLogRepository;
import cn.mjy.platform.shared.TenantId;
import cn.mjy.platform.shared.tenant.TenantScope;
import cn.mjy.platform.tenant.api.CreateResult;
import java.util.Optional;
import java.util.UUID;
import org.springframework.stereotype.Service;

/**
 * 外部身份绑定（最小模型）。同一外部身份在每个租户内只绑定一次，重复绑定幂等返回；
 * 不同租户各自绑定、互不相关（见 V104），因此绑定结果不会透露此人是否存在于别的租户。
 */
@Service
public class IdentityBindingService {

    private static final String ACTION_BIND = "identity.bind";

    private final TenantScope tenantScope;
    private final IdentityBindingRepository bindings;
    private final AuditLogRepository audit;

    public IdentityBindingService(TenantScope tenantScope, IdentityBindingRepository bindings,
                                  AuditLogRepository audit) {
        this.tenantScope = tenantScope;
        this.bindings = bindings;
        this.audit = audit;
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
