package cn.mjy.platform.tenant;

import cn.mjy.platform.shared.TenantId;
import java.time.OffsetDateTime;

/** 租户登记（控制平面）。不可变：状态变化返回新对象。 */
public record Tenant(TenantId id, String code, String name, TenantStatus status, OffsetDateTime createdAt) {

    public Tenant withStatus(TenantStatus next) {
        return new Tenant(id, code, name, next, createdAt);
    }
}
