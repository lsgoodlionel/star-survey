package cn.mjy.platform.tenant.engine;

import cn.mjy.platform.audit.AuditLogRepository;
import cn.mjy.platform.shared.TenantId;
import cn.mjy.platform.shared.tenant.TenantScope;
import cn.mjy.platform.tenant.Tenant;
import cn.mjy.platform.tenant.TenantRepository;
import cn.mjy.platform.tenant.TenantStatus;
import cn.mjy.platform.tenant.api.ConflictException;
import cn.mjy.platform.tenant.api.NotFoundException;
import java.util.List;
import java.util.Optional;
import org.springframework.stereotype.Service;

/** 引擎实例登记与状态管理。所有读写都在所属租户的 {@link TenantScope} 内完成，写操作留审计。 */
@Service
public class EngineInstanceService {

    private static final String ACTION_REGISTER = "engine_instance.register";
    private static final String ACTION_STATUS = "engine_instance.status:";

    private final TenantScope tenantScope;
    private final TenantRepository tenants;
    private final EngineInstanceRepository instances;
    private final AuditLogRepository audit;

    public EngineInstanceService(TenantScope tenantScope, TenantRepository tenants,
                                 EngineInstanceRepository instances, AuditLogRepository audit) {
        this.tenantScope = tenantScope;
        this.tenants = tenants;
        this.instances = instances;
        this.audit = audit;
    }

    /** 为租户登记一套新实例（初始 active）。租户必须存在且未关闭；实例标识全局唯一。 */
    public EngineInstance register(TenantId tenant, String instanceId, String baseUrl, String actorId, String traceId) {
        return tenantScope.call(tenant, () -> {
            Tenant owner = tenants.findById(tenant)
                    .orElseThrow(() -> new NotFoundException("tenant not found: " + tenant));
            if (owner.status() == TenantStatus.CLOSED) {
                throw new ConflictException("tenant is closed: " + tenant);
            }
            instances.insert(tenant, instanceId, baseUrl);
            audit.record(tenant, actorId, ACTION_REGISTER, resource(instanceId), traceId);
            return instances.find(instanceId).orElseThrow();
        });
    }

    public EngineInstance changeStatus(TenantId tenant, String instanceId, EngineInstanceStatus target,
                                       String actorId, String traceId) {
        return tenantScope.call(tenant, () -> {
            EngineInstance current = instances.find(instanceId)
                    .orElseThrow(() -> notFound(instanceId));
            EngineInstanceStatus from = current.status();
            from.transitionTo(target);
            if (!instances.updateStatus(instanceId, from, target)) {
                throw new ConflictException("engine instance status changed concurrently, retry");
            }
            audit.record(tenant, actorId, ACTION_STATUS + from.code() + "->" + target.code(),
                    resource(instanceId), traceId);
            return current.withStatus(target);
        });
    }

    public List<EngineInstance> list(TenantId tenant) {
        return tenantScope.call(tenant, instances::listAll);
    }

    public Optional<EngineInstance> find(TenantId tenant, String instanceId) {
        return tenantScope.call(tenant, () -> instances.find(instanceId));
    }

    public static NotFoundException notFound(String instanceId) {
        return new NotFoundException("engine instance not found: " + instanceId);
    }

    private static String resource(String instanceId) {
        return "engine_instance/" + instanceId;
    }
}
