package cn.mjy.platform.entitlement;

import cn.mjy.platform.shared.TenantId;
import java.time.Duration;
import java.time.Instant;
import java.util.Objects;
import java.util.UUID;

/** 一个订阅期：绑定一个套餐版本，落库后不再改写。 */
public record Subscription(UUID id, TenantId tenantId, UUID planVersionId, SubscriptionKind kind,
                           Instant startsAt, Instant endsAt) {

    public Subscription {
        Objects.requireNonNull(id, "id");
        Objects.requireNonNull(tenantId, "tenantId");
        Objects.requireNonNull(planVersionId, "planVersionId");
        Objects.requireNonNull(kind, "kind");
        if (endsAt.isBefore(startsAt)) {
            throw new IllegalArgumentException("subscription must not end before it starts");
        }
    }

    public SubscriptionStatus statusAt(Instant at) {
        if (kind == SubscriptionKind.CANCELLED) {
            return SubscriptionStatus.CANCELLED;
        }
        if (!at.isBefore(endsAt)) {
            return SubscriptionStatus.EXPIRED;
        }
        return kind == SubscriptionKind.TRIAL ? SubscriptionStatus.TRIAL : SubscriptionStatus.ACTIVE;
    }

    /** 进入只读的时刻：取消期从取消时起，其余从到期时起。 */
    public Instant readOnlySince() {
        return kind == SubscriptionKind.CANCELLED ? startsAt : endsAt;
    }

    /** 只读后导出窗口的截止时刻；窗口过后数据仍保留、仍可查看，只是不再允许导出。 */
    public Instant exportWindowEndsAt(int exportWindowDays) {
        return readOnlySince().plus(Duration.ofDays(exportWindowDays));
    }
}
