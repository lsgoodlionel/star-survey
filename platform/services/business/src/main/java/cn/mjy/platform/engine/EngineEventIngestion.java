package cn.mjy.platform.engine;

import cn.mjy.platform.shared.TenantId;
import cn.mjy.platform.shared.engine.EngineInstanceDirectory;
import cn.mjy.platform.shared.tenant.TenantScope;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import java.util.Set;
import org.springframework.beans.factory.ObjectProvider;
import org.springframework.stereotype.Service;

/**
 * 处理一批已验签的引擎事件。
 *
 * <ol>
 *   <li>先把整批转换、校验完，任何一条不合格就整批拒收；</li>
 *   <li>逐个实例查目录得到租户——只信目录，不信事件体；有任何未知实例就整批拒收，此时一行未写；</li>
 *   <li>按租户分组，每组在该租户的 {@link TenantScope} 事务里应用，行级安全兜底。</li>
 * </ol>
 * 一批通常只含一个实例（一个引擎库对应一个实例）。若混有多个租户，各租户分别提交；
 * 中途失败时已提交的部分在重投时被判为重复，不会产生双重效果。
 *
 * <p>目录由租户模块实现。尚未提供实现时本接口拒收一切（失败即关闭），不影响应用启动与其他模块。
 */
@Service
public class EngineEventIngestion {

    private final ObjectProvider<EngineInstanceDirectory> directory;
    private final TenantScope tenantScope;
    private final EngineEventApplier applier;

    public EngineEventIngestion(ObjectProvider<EngineInstanceDirectory> directory, TenantScope tenantScope,
            EngineEventApplier applier) {
        this.directory = directory;
        this.tenantScope = tenantScope;
        this.applier = applier;
    }

    public IngestionResult ingest(EngineEventBatch batch) {
        List<EngineEvent> events = batch.events().stream().map(EngineEvent::from).toList();
        Map<TenantId, List<EngineEvent>> byTenant = groupByTenant(events, resolveOwners(events));
        IngestionResult result = IngestionResult.EMPTY;
        for (Map.Entry<TenantId, List<EngineEvent>> group : byTenant.entrySet()) {
            TenantId tenant = group.getKey();
            result = result.plus(tenantScope.call(tenant, () -> applier.applyAll(tenant, group.getValue())));
        }
        return result;
    }

    private Map<String, TenantId> resolveOwners(List<EngineEvent> events) {
        EngineInstanceDirectory instances = directory.getIfAvailable();
        if (instances == null) {
            throw new EngineDirectoryUnavailableException();
        }
        Map<String, TenantId> owners = new LinkedHashMap<>();
        Set<String> unknown = new LinkedHashSet<>();
        for (EngineEvent event : events) {
            String instance = event.engineInstanceId();
            if (owners.containsKey(instance) || unknown.contains(instance)) {
                continue;
            }
            Optional<TenantId> owner = instances.tenantOf(instance);
            owner.ifPresentOrElse(tenant -> owners.put(instance, tenant), () -> unknown.add(instance));
        }
        if (!unknown.isEmpty()) {
            throw new UnknownEngineInstanceException(unknown);
        }
        return owners;
    }

    private static Map<TenantId, List<EngineEvent>> groupByTenant(List<EngineEvent> events,
            Map<String, TenantId> owners) {
        Map<TenantId, List<EngineEvent>> groups = new LinkedHashMap<>();
        for (EngineEvent event : events) {
            groups.computeIfAbsent(owners.get(event.engineInstanceId()), tenant -> new ArrayList<>()).add(event);
        }
        return groups;
    }
}
