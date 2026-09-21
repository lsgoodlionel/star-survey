package cn.mjy.platform.entitlement;

import java.util.Map;
import java.util.Objects;
import java.util.Set;
import java.util.regex.Pattern;

/**
 * 待发布的套餐内容：能力开关与各计量额度。发布后成为不可变的 {@link PlanVersion}。
 * 这里只做形状校验；能力与计量是否已登记由 {@link PlanCatalog#publish} 对照数据库校验。
 */
public record PlanDefinition(String planCode, Set<String> capabilities, Map<String, Long> quotas,
                             int exportWindowDays) {

    /** 能力与计量代码的形状；只允许这些字符，拼进 JSON 时无需转义。 */
    static final Pattern CODE = Pattern.compile("[a-z][a-z0-9_.]{1,63}");
    private static final Pattern PLAN_CODE = Pattern.compile("[a-z][a-z0-9_.-]{1,63}");

    public PlanDefinition {
        Objects.requireNonNull(planCode, "planCode");
        if (!PLAN_CODE.matcher(planCode).matches()) {
            throw new IllegalArgumentException("invalid plan code: " + planCode);
        }
        capabilities = Set.copyOf(capabilities);
        quotas = Map.copyOf(quotas);
        capabilities.forEach(code -> requireCode("capability", code));
        quotas.forEach(PlanDefinition::requireQuota);
        if (exportWindowDays < 0) {
            throw new IllegalArgumentException("exportWindowDays must not be negative");
        }
    }

    private static void requireQuota(String meter, Long limit) {
        requireCode("meter", meter);
        if (limit < 0) {
            throw new IllegalArgumentException("quota for " + meter + " must not be negative");
        }
    }

    private static void requireCode(String kind, String code) {
        if (!CODE.matcher(code).matches()) {
            throw new IllegalArgumentException("invalid " + kind + " code: " + code);
        }
    }
}
