package cn.mjy.platform.identity;

import cn.mjy.platform.audit.AuditLogRepository;
import cn.mjy.platform.shared.TenantId;
import cn.mjy.platform.shared.tenant.TenantScope;
import cn.mjy.platform.tenant.api.ConflictException;
import cn.mjy.platform.tenant.api.CreateResult;
import java.util.Optional;
import java.util.UUID;
import org.springframework.stereotype.Service;

/**
 * 外部身份绑定（最小模型）。一个三元组全局只绑定一次：在本租户已绑定则幂等返回；
 * 已被别的租户绑定则 409，且不透露对方的任何信息。
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
            // 冲突的既有行在本租户可见 = 本租户重复绑定；不可见 = 属于别的租户。
            return bindings.findByIdentity(identity)
                    .map(existing -> new CreateResult<>(existing, false))
                    .orElseThrow(() -> new ConflictException("identity is already bound"));
        });
    }

    public Optional<IdentityBinding> findByIdentity(TenantId tenant, ExternalIdentity identity) {
        return tenantScope.call(tenant, () -> bindings.findByIdentity(identity));
    }

    public Optional<IdentityBinding> findByPrincipal(TenantId tenant, UUID principalId) {
        return tenantScope.call(tenant, () -> bindings.findByPrincipal(principalId));
    }
}
