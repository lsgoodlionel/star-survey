package cn.mjy.platform.tenant.routing;

import cn.mjy.platform.audit.AuditLogRepository;
import cn.mjy.platform.shared.TenantId;
import cn.mjy.platform.shared.tenant.TenantScope;
import cn.mjy.platform.tenant.api.ConflictException;
import cn.mjy.platform.tenant.api.CreateResult;
import cn.mjy.platform.tenant.engine.EngineInstance;
import cn.mjy.platform.tenant.engine.EngineInstanceRepository;
import cn.mjy.platform.tenant.engine.EngineInstanceService;
import cn.mjy.platform.tenant.engine.EngineInstanceStatus;
import java.util.Optional;
import java.util.UUID;
import org.springframework.stereotype.Service;

/**
 * 问卷路由：对外问卷 UUID ⇄ 引擎实例 ⇄ 引擎 sid。所有读写都在调用方租户的 {@link TenantScope} 内完成，
 * 因此按 UUID 查找永远查不到别的租户的路由。
 */
@Service
public class SurveyRouteService {

    private static final String ACTION_CREATE = "survey_route.create";

    private final TenantScope tenantScope;
    private final SurveyRouteRepository routes;
    private final EngineInstanceRepository instances;
    private final AuditLogRepository audit;

    public SurveyRouteService(TenantScope tenantScope, SurveyRouteRepository routes,
                              EngineInstanceRepository instances, AuditLogRepository audit) {
        this.tenantScope = tenantScope;
        this.routes = routes;
        this.instances = instances;
        this.audit = audit;
    }

    /** 幂等：同一 (实例, sid) 重复创建返回既有路由。实例必须是本租户的且处于 active。 */
    public CreateResult<SurveyRoute> create(TenantId tenant, String instanceId, int sid, String actorId, String traceId) {
        return tenantScope.call(tenant, () -> {
            EngineInstance instance = instances.find(instanceId)
                    .orElseThrow(() -> EngineInstanceService.notFound(instanceId));
            if (instance.status() != EngineInstanceStatus.ACTIVE) {
                throw new ConflictException("engine instance is not active: " + instanceId);
            }
            Optional<SurveyRoute> inserted = routes.insertIfAbsent(tenant, instanceId, sid);
            if (inserted.isEmpty()) {
                return new CreateResult<>(routes.findByEngine(instanceId, sid).orElseThrow(), false);
            }
            SurveyRoute route = inserted.get();
            audit.record(tenant, actorId, ACTION_CREATE, "survey_route/" + route.publicId(), traceId);
            return new CreateResult<>(route, true);
        });
    }

    public Optional<SurveyRoute> findByPublicId(TenantId tenant, UUID publicId) {
        return tenantScope.call(tenant, () -> routes.findByPublicId(publicId));
    }

    public Optional<SurveyRoute> findByEngine(TenantId tenant, String instanceId, int sid) {
        return tenantScope.call(tenant, () -> routes.findByEngine(instanceId, sid));
    }
}
