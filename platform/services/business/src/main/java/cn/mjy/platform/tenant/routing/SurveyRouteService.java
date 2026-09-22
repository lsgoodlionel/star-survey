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
    private static final String ACTION_SWITCH = "survey_route.switch";

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
            requireActiveInstance(instanceId);
            Optional<SurveyRoute> inserted = routes.insertIfAbsent(tenant, instanceId, sid);
            if (inserted.isEmpty()) {
                return new CreateResult<>(routes.findByEngine(instanceId, sid).orElseThrow(), false);
            }
            SurveyRoute route = inserted.get();
            audit.record(tenant, actorId, ACTION_CREATE, "survey_route/" + route.publicId(), traceId);
            return new CreateResult<>(route, true);
        });
    }

    /**
     * 发布流程专用：以问卷自己的公开 UUID 登记路由，使对外 UUID 在发布前后保持不变。
     * 幂等：同一 (公开 UUID, 实例, sid) 重复登记返回既有路由；公开 UUID 已指向别处、
     * 或该 (实例, sid) 已挂在别的公开 UUID 下时 409。在调用方事务内执行时与之同成同败。
     */
    public SurveyRoute registerPublished(TenantId tenant, UUID publicId, String instanceId, int sid,
                                         String actorId, String traceId) {
        return tenantScope.call(tenant, () -> {
            requireActiveInstance(instanceId);
            Optional<SurveyRoute> inserted = routes.insertWithPublicIdIfAbsent(tenant, publicId, instanceId, sid);
            if (inserted.isPresent()) {
                audit.record(tenant, actorId, ACTION_CREATE, "survey_route/" + publicId, traceId);
                return inserted.get();
            }
            return routes.findByPublicId(publicId)
                    .filter(existing -> existing.engineInstanceId().equals(instanceId) && existing.engineSid() == sid)
                    .orElseThrow(() -> new ConflictException(
                            "public id or engine survey is already routed elsewhere: " + publicId));
        });
    }

    /**
     * 重新发布专用：把公开 UUID 的当前路由从 (fromInstance, fromSid) 原子地换到 (toInstance, toSid)（ADR 0012）。
     * 旧路由只标记为被取代、不删除，旧 sid 仍能反查到公开 UUID。幂等：当前路由已经是目标时原样返回。
     * 当前路由不是 from（别人已经切过或从未登记）、或目标 (实例, sid) 已挂在别处时 409。
     * 在调用方事务内执行时与之同成同败。
     */
    public SurveyRoute switchPublished(TenantId tenant, UUID publicId, String fromInstance, int fromSid,
                                       String toInstance, int toSid, String actorId, String traceId) {
        return tenantScope.call(tenant, () -> {
            requireActiveInstance(toInstance);
            SurveyRoute current = routes.lockCurrent(publicId)
                    .orElseThrow(() -> new ConflictException("survey has no current route: " + publicId));
            if (current.engineInstanceId().equals(toInstance) && current.engineSid() == toSid) {
                return current;
            }
            if (!current.engineInstanceId().equals(fromInstance) || current.engineSid() != fromSid
                    || !routes.supersede(publicId, fromInstance, fromSid)) {
                throw new ConflictException("current route of " + publicId + " is not " + fromInstance + "/" + fromSid);
            }
            SurveyRoute switched = routes.insertWithPublicIdIfAbsent(tenant, publicId, toInstance, toSid)
                    .orElseThrow(() -> new ConflictException(
                            "engine survey " + toInstance + "/" + toSid + " is already routed elsewhere"));
            audit.record(tenant, actorId, ACTION_SWITCH, "survey_route/" + publicId + " from=" + fromInstance + "/"
                    + fromSid + " to=" + toInstance + "/" + toSid, traceId);
            return switched;
        });
    }

    private void requireActiveInstance(String instanceId) {
        EngineInstance instance = instances.find(instanceId)
                .orElseThrow(() -> EngineInstanceService.notFound(instanceId));
        if (instance.status() != EngineInstanceStatus.ACTIVE) {
            throw new ConflictException("engine instance is not active: " + instanceId);
        }
    }

    public Optional<SurveyRoute> findByPublicId(TenantId tenant, UUID publicId) {
        return tenantScope.call(tenant, () -> routes.findByPublicId(publicId));
    }

    public Optional<SurveyRoute> findByEngine(TenantId tenant, String instanceId, int sid) {
        return tenantScope.call(tenant, () -> routes.findByEngine(instanceId, sid));
    }
}
