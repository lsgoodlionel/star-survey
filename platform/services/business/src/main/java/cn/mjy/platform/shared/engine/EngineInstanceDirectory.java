package cn.mjy.platform.shared.engine;

import cn.mjy.platform.shared.TenantId;
import java.util.Optional;

/**
 * 引擎实例归属查询（跨模块契约）。由租户模块实现；引擎事件模块据此判定事件属于哪个租户，
 * 绝不信任事件体里自称的租户。未登记或已停用的实例返回空。
 */
public interface EngineInstanceDirectory {

    Optional<TenantId> tenantOf(String engineInstanceId);
}
