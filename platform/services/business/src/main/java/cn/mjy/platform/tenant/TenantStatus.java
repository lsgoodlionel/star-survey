package cn.mjy.platform.tenant;

import cn.mjy.platform.tenant.api.IllegalTransitionException;
import java.util.EnumSet;
import java.util.Locale;
import java.util.Set;

/**
 * 租户生命周期状态机：
 * <pre>
 *   provisioning ──► active ◄──► suspended
 *        │             │            │
 *        └─────────────┴──► closed ◄┘   （closed 为终态）
 * </pre>
 * 数据库里有同样规则的触发器兜底（V100），两处必须保持一致。
 */
public enum TenantStatus {
    PROVISIONING,
    ACTIVE,
    SUSPENDED,
    CLOSED;

    private Set<TenantStatus> successors() {
        return switch (this) {
            case PROVISIONING -> EnumSet.of(ACTIVE, CLOSED);
            case ACTIVE -> EnumSet.of(SUSPENDED, CLOSED);
            case SUSPENDED -> EnumSet.of(ACTIVE, CLOSED);
            case CLOSED -> EnumSet.noneOf(TenantStatus.class);
        };
    }

    public boolean canTransitionTo(TenantStatus target) {
        return successors().contains(target);
    }

    /** 返回目标状态；不允许的迁移抛出 {@link IllegalTransitionException}。 */
    public TenantStatus transitionTo(TenantStatus target) {
        if (!canTransitionTo(target)) {
            throw new IllegalTransitionException("tenant", code(), target.code());
        }
        return target;
    }

    /** 对外与数据库中使用的小写编码。 */
    public String code() {
        return name().toLowerCase(Locale.ROOT);
    }

    public static TenantStatus fromCode(String code) {
        if (code != null) {
            for (TenantStatus status : values()) {
                if (status.code().equals(code)) {
                    return status;
                }
            }
        }
        throw new IllegalArgumentException("unknown tenant status: " + code);
    }
}
