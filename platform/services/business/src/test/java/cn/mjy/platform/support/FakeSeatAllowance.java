package cn.mjy.platform.support;

import cn.mjy.platform.shared.SeatAllowance;
import cn.mjy.platform.shared.TenantId;
import java.util.Map;
import java.util.concurrent.ConcurrentHashMap;
import org.springframework.context.annotation.Primary;
import org.springframework.stereotype.Component;

/**
 * 测试用席位额度：额度模块并行开发中，这里按租户给固定上限。
 * 标 {@code @Primary}，集成真实实现后测试仍以本替身为准。
 */
@Primary
@Component
public class FakeSeatAllowance implements SeatAllowance {

    private static final int DEFAULT_SEATS = 50;

    private final Map<TenantId, Integer> limits = new ConcurrentHashMap<>();

    public void setLimit(TenantId tenant, int seats) {
        limits.put(tenant, seats);
    }

    @Override
    public int seatLimit(TenantId tenant) {
        return limits.getOrDefault(tenant, DEFAULT_SEATS);
    }
}
