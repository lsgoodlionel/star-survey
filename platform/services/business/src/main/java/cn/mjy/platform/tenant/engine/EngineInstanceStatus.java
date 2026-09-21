package cn.mjy.platform.tenant.engine;

import cn.mjy.platform.tenant.api.IllegalTransitionException;
import java.util.EnumSet;
import java.util.Locale;
import java.util.Set;

/**
 * 引擎实例状态：active（接流量）⇄ draining（停止接新流量，等待收尾）→ retired（下线，终态）。
 * 只有 active 实例会被 {@link cn.mjy.platform.shared.engine.EngineInstanceDirectory} 解析出租户。
 */
public enum EngineInstanceStatus {
    ACTIVE,
    DRAINING,
    RETIRED;

    private Set<EngineInstanceStatus> successors() {
        return switch (this) {
            case ACTIVE -> EnumSet.of(DRAINING, RETIRED);
            case DRAINING -> EnumSet.of(ACTIVE, RETIRED);
            case RETIRED -> EnumSet.noneOf(EngineInstanceStatus.class);
        };
    }

    public EngineInstanceStatus transitionTo(EngineInstanceStatus target) {
        if (!successors().contains(target)) {
            throw new IllegalTransitionException("engine instance", code(), target.code());
        }
        return target;
    }

    public String code() {
        return name().toLowerCase(Locale.ROOT);
    }

    public static EngineInstanceStatus fromCode(String code) {
        if (code != null) {
            for (EngineInstanceStatus status : values()) {
                if (status.code().equals(code)) {
                    return status;
                }
            }
        }
        throw new IllegalArgumentException("unknown engine instance status: " + code);
    }
}
