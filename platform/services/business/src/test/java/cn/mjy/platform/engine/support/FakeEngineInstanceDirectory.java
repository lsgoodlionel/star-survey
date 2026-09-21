package cn.mjy.platform.engine.support;

import cn.mjy.platform.shared.TenantId;
import cn.mjy.platform.shared.engine.EngineInstanceDirectory;
import java.util.Map;
import java.util.Optional;
import java.util.UUID;
import java.util.concurrent.ConcurrentHashMap;

/**
 * 测试用引擎实例登记：租户车道并行实现真实目录，这里只提供"实例 → 租户"的最小映射。
 * 每个测试登记随机实例名，互不干扰。
 */
public class FakeEngineInstanceDirectory implements EngineInstanceDirectory {

    private final Map<String, TenantId> owners = new ConcurrentHashMap<>();

    public String register(TenantId tenant) {
        String instance = "engine-" + UUID.randomUUID();
        register(instance, tenant);
        return instance;
    }

    public void register(String engineInstanceId, TenantId tenant) {
        owners.put(engineInstanceId, tenant);
    }

    @Override
    public Optional<TenantId> tenantOf(String engineInstanceId) {
        return Optional.ofNullable(owners.get(engineInstanceId));
    }
}
