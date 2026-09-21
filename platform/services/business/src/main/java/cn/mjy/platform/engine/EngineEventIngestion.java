package cn.mjy.platform.engine;

import cn.mjy.platform.shared.TenantId;
import cn.mjy.platform.shared.engine.EngineInstanceDirectory;
import cn.mjy.platform.shared.tenant.TenantScope;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Set;
import java.util.stream.Collectors;
import org.springframework.beans.factory.ObjectProvider;
import org.springframework.stereotype.Service;

/**
 * 处理一批已验签的引擎事件。
 *
 * <ol>
 *   <li>先核对实例：每个事件的 {@code engineInstanceId} 都必须等于验签确认的实例，否则整批拒收
 *       ——签名只证明发送方持有该实例的密钥，不能让它替别的实例（别的租户）写事件；</li>
 *   <li>再把整批转换、校验完，任何一条不合格就整批拒收；</li>
 *   <li>查目录得到该实例的租户——只信目录，不信事件体；实例未登记（或已停用）就整批拒收，此时一行未写；</li>
 *   <li>在该租户的 {@link TenantScope} 事务里应用整批，行级安全兜底。</li>
 * </ol>
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

    /**
     * @param authenticatedInstance 验签确认的实例（来自过滤器写入的请求属性）
     */
    public IngestionResult ingest(String authenticatedInstance, EngineEventBatch batch) {
        requireSameInstance(authenticatedInstance, batch);
        List<EngineEvent> events = batch.events().stream().map(EngineEvent::from).toList();
        EngineInstanceDirectory instances = directory.getIfAvailable();
        if (instances == null) {
            throw new EngineDirectoryUnavailableException();
        }
        if (events.isEmpty()) {
            return IngestionResult.EMPTY;
        }
        TenantId tenant = instances.tenantOf(authenticatedInstance)
                .orElseThrow(() -> new UnknownEngineInstanceException(Set.of(authenticatedInstance)));
        return tenantScope.call(tenant, () -> applier.applyAll(tenant, events));
    }

    private static void requireSameInstance(String authenticatedInstance, EngineEventBatch batch) {
        Set<String> foreign = batch.events().stream()
                .map(EngineEventBatch.Envelope::engineInstanceId)
                .filter(claimed -> !claimed.equals(authenticatedInstance))
                .collect(Collectors.toCollection(LinkedHashSet::new));
        if (!foreign.isEmpty()) {
            throw new EngineInstanceMismatchException(authenticatedInstance, foreign);
        }
    }
}
