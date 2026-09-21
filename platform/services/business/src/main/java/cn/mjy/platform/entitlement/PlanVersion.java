package cn.mjy.platform.entitlement;

import java.time.Instant;
import java.util.Map;
import java.util.OptionalLong;
import java.util.Set;
import java.util.UUID;

/** 已发布的套餐版本，不可变。改动套餐等于发布新版本，已绑定旧版本的订阅不受影响。 */
public record PlanVersion(UUID id, String planCode, int version, Set<String> capabilities,
                          Map<String, Long> quotas, int exportWindowDays, Instant publishedAt) {

    public PlanVersion {
        capabilities = Set.copyOf(capabilities);
        quotas = Map.copyOf(quotas);
    }

    public boolean enables(String capability) {
        return capabilities.contains(capability);
    }

    /** 该计量的额度上限；套餐未配置时为空，调用方按"没有额度"处理（失败即关闭）。 */
    public OptionalLong quotaFor(String meter) {
        Long limit = quotas.get(meter);
        return limit == null ? OptionalLong.empty() : OptionalLong.of(limit);
    }
}
