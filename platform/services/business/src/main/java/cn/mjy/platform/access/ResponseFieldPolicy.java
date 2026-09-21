package cn.mjy.platform.access;

import java.util.Collections;
import java.util.LinkedHashMap;
import java.util.Map;
import java.util.Set;

/**
 * 一次判定得出的答卷字段可见性，可对多条记录重复套用（导出时逐行调用，不重复判定）。
 * 纯函数：总是返回新的不可变副本，从不修改传入记录。
 *
 * <p>没有"查看敏感字段"权限时，敏感字段的非空值整体替换为固定掩码——不保留前后缀、不暴露长度；
 * 空值与缺失字段保持原样。
 */
public final class ResponseFieldPolicy {

    public static final String MASK = "******";

    private final Set<String> sensitiveFields;
    private final boolean revealSensitive;

    ResponseFieldPolicy(Set<String> sensitiveFields, boolean revealSensitive) {
        this.sensitiveFields = Set.copyOf(sensitiveFields);
        this.revealSensitive = revealSensitive;
    }

    public boolean revealsSensitiveFields() {
        return revealSensitive;
    }

    public Map<String, String> apply(Map<String, String> record) {
        Map<String, String> copy = new LinkedHashMap<>(record);
        if (!revealSensitive) {
            copy.replaceAll((field, value) -> value != null && sensitiveFields.contains(field) ? MASK : value);
        }
        return Collections.unmodifiableMap(copy);
    }
}
