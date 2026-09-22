package cn.mjy.platform.response;

import java.util.Collections;
import java.util.LinkedHashMap;
import java.util.Map;

/**
 * 一次读取取回的作答：答卷号 → (引擎列名 → 值)。引擎里已不存在的答卷不出现在映射里。
 * 值可以为 {@code null}（未作答），因此内层用允许空值的不可变视图而不是 {@link Map#copyOf}。
 */
public record AnswerBatch(Map<Long, Map<String, String>> answers) {

    public AnswerBatch {
        Map<Long, Map<String, String>> copy = new LinkedHashMap<>();
        answers.forEach((id, values) -> copy.put(id, Collections.unmodifiableMap(new LinkedHashMap<>(values))));
        answers = Collections.unmodifiableMap(copy);
    }

    public static AnswerBatch empty() {
        return new AnswerBatch(Map.of());
    }
}
