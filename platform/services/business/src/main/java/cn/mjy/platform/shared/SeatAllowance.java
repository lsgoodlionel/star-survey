package cn.mjy.platform.shared;

/**
 * 租户可用的成员席位上限（跨模块契约）。由额度模块（entitlement）按套餐与加购实现；
 * 权限模块（access）只在邀请成员时读取它，席位占用数由权限模块自己计数并在事务内加锁判定。
 *
 * <p>约定：返回值是"当前生效的上限"，不做缓存假设，每次邀请都会调用；
 * 返回 0 或负数表示没有可用席位。实现缺失时权限模块失败即关闭（不允许邀请）。
 */
@FunctionalInterface
public interface SeatAllowance {

    int seatLimit(TenantId tenant);
}
