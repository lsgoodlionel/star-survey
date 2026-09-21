package cn.mjy.platform.tenant;

import cn.mjy.platform.audit.AuditLogRepository;
import cn.mjy.platform.shared.TenantId;
import cn.mjy.platform.shared.tenant.TenantScope;
import cn.mjy.platform.tenant.api.ConflictException;
import cn.mjy.platform.tenant.api.NotFoundException;
import java.util.Optional;
import org.springframework.stereotype.Service;

/**
 * 租户开通与状态变更（控制平面）。调用方的运营身份由控制器层校验；
 * 这里保证每次变更与其审计记录在同一个事务里提交——要么都有，要么都没有。
 */
@Service
public class TenantService {

    private static final String ACTION_CREATE = "tenant.create";
    private static final String ACTION_STATUS = "tenant.status:";

    private final TenantScope tenantScope;
    private final TenantRepository tenants;
    private final AuditLogRepository audit;

    public TenantService(TenantScope tenantScope, TenantRepository tenants, AuditLogRepository audit) {
        this.tenantScope = tenantScope;
        this.tenants = tenants;
        this.audit = audit;
    }

    /** 新租户一律从 provisioning 开始。编码重复时由数据库唯一约束拒绝。 */
    public Tenant create(String code, String name, String actorId, String traceId) {
        TenantId id = TenantId.random();
        return tenantScope.call(id, () -> {
            tenants.insert(id, code, name);
            audit.record(id, actorId, ACTION_CREATE, resource(id), traceId);
            return tenants.findById(id).orElseThrow();
        });
    }

    public Optional<Tenant> find(TenantId id) {
        return tenantScope.call(id, () -> tenants.findById(id));
    }

    /** 按状态机迁移；非法迁移抛 409，不写审计。 */
    public Tenant changeStatus(TenantId id, TenantStatus target, String actorId, String traceId) {
        return tenantScope.call(id, () -> {
            Tenant current = tenants.findById(id)
                    .orElseThrow(() -> new NotFoundException("tenant not found: " + id));
            TenantStatus from = current.status();
            from.transitionTo(target);
            if (!tenants.updateStatus(id, from, target)) {
                throw new ConflictException("tenant status changed concurrently, retry");
            }
            audit.record(id, actorId, ACTION_STATUS + from.code() + "->" + target.code(), resource(id), traceId);
            return current.withStatus(target);
        });
    }

    private static String resource(TenantId id) {
        return "tenant/" + id;
    }
}
