package cn.mjy.platform.shared.tenant;

import cn.mjy.platform.shared.TenantId;
import java.util.List;

/**
 * 租户枚举（跨模块契约）。由租户模块实现，供需要"逐个租户做后台工作"的任务使用（如发布核对）：
 * 调用方拿到租户标识后，必须逐个进入 {@link TenantScope} 再访问租户数据——本接口只给出名单，
 * 不给出任何租户数据，也不绕开行级安全。
 */
public interface TenantDirectory {

    /** 尚未关闭（provisioning / active / suspended）的租户，按登记顺序。 */
    List<TenantId> openTenants();
}
