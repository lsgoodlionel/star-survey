package cn.mjy.platform.access;

import cn.mjy.platform.shared.SeatAllowance;
import cn.mjy.platform.shared.TenantId;
import org.springframework.beans.factory.ObjectProvider;
import org.springframework.stereotype.Component;

/**
 * 席位判定。上限来自额度模块的 {@link SeatAllowance}；实现缺失时失败即关闭（没有可用席位）。
 * 调用方须已持有租户成员锁（{@link TenantAccessLock}），否则并发邀请可能同时通过。
 */
@Component
class SeatGuard {

    private final ObjectProvider<SeatAllowance> allowance;

    SeatGuard(ObjectProvider<SeatAllowance> allowance) {
        this.allowance = allowance;
    }

    void requireFreeSeat(TenantId tenant, long seatsInUse) {
        SeatAllowance source = allowance.getIfAvailable();
        if (source == null) {
            throw new SeatLimitExceededException("no seat allowance is configured for tenant " + tenant);
        }
        int limit = Math.max(0, source.seatLimit(tenant));
        if (seatsInUse >= limit) {
            throw new SeatLimitExceededException(
                    "seat limit reached for tenant " + tenant + ": " + seatsInUse + " of " + limit + " in use");
        }
    }
}
