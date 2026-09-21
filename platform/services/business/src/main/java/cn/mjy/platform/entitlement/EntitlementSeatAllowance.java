package cn.mjy.platform.entitlement;

import cn.mjy.platform.shared.SeatAllowance;
import cn.mjy.platform.shared.TenantId;
import java.time.Instant;
import java.util.Optional;
import org.springframework.stereotype.Component;

/**
 * 权限模块所需的席位上限（{@link SeatAllowance} 的真实实现）：取当前订阅所用套餐的
 * {@code member.seats} 额度。
 *
 * <p>任何给不出明确额度的情况都返回 0，由权限模块拒绝邀请（失败即关闭）：没有订阅、
 * 订阅已只读（到期或取消后不再允许新增成员）、套餐未配置席位。
 */
@Component
public class EntitlementSeatAllowance implements SeatAllowance {

    private final SubscriptionService subscriptions;
    private final PlanCatalog plans;

    public EntitlementSeatAllowance(SubscriptionService subscriptions, PlanCatalog plans) {
        this.subscriptions = subscriptions;
        this.plans = plans;
    }

    @Override
    public int seatLimit(TenantId tenant) {
        Optional<Subscription> current = subscriptions.current(tenant);
        if (current.isEmpty() || current.get().statusAt(Instant.now()).isReadOnly()) {
            return 0;
        }
        long quota = plans.find(current.get().planVersionId())
                .flatMap(plan -> {
                    var limit = plan.quotaFor(Meters.MEMBER_SEATS);
                    return limit.isPresent() ? Optional.of(limit.getAsLong()) : Optional.empty();
                })
                .orElse(0L);
        return (int) Math.max(0, Math.min(quota, Integer.MAX_VALUE));
    }
}
