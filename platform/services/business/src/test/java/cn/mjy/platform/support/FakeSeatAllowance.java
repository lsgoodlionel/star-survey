package cn.mjy.platform.support;

import cn.mjy.platform.entitlement.EntitlementSeatAllowance;
import cn.mjy.platform.entitlement.SubscriptionService;
import cn.mjy.platform.shared.SeatAllowance;
import cn.mjy.platform.shared.TenantId;
import java.util.Map;
import java.util.concurrent.ConcurrentHashMap;
import org.springframework.context.annotation.Primary;
import org.springframework.stereotype.Component;

/**
 * 测试用席位额度。
 *
 * <p>优先级：测试显式设定的上限 &gt; 有真实订阅的租户走真实实现 {@link EntitlementSeatAllowance}
 * &gt; 其余（没有订阅的测试租户）默认 {@value #DEFAULT_SEATS} 个。
 *
 * <p>早先它对所有租户一律给 50，导致没有任何测试让权限模块与额度模块真正联动；
 * 现在凡是开了订阅的租户，席位上限都来自套餐，与生产一致。
 */
@Primary
@Component
public class FakeSeatAllowance implements SeatAllowance {

    private static final int DEFAULT_SEATS = 50;

    private final Map<TenantId, Integer> limits = new ConcurrentHashMap<>();
    private final EntitlementSeatAllowance real;
    private final SubscriptionService subscriptions;

    public FakeSeatAllowance(EntitlementSeatAllowance real, SubscriptionService subscriptions) {
        this.real = real;
        this.subscriptions = subscriptions;
    }

    public void setLimit(TenantId tenant, int seats) {
        limits.put(tenant, seats);
    }

    @Override
    public int seatLimit(TenantId tenant) {
        Integer explicit = limits.get(tenant);
        if (explicit != null) {
            return explicit;
        }
        return subscriptions.current(tenant).isPresent() ? real.seatLimit(tenant) : DEFAULT_SEATS;
    }
}
