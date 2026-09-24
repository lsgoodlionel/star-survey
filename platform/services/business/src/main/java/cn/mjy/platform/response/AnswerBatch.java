package cn.mjy.platform.response;

import java.util.Collections;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Objects;

/**
 * 一次读取取回的作答。引擎里已不存在的答卷不出现在映射里。
 *
 * <p>两段分开放，与网关的应答同构（契约 response-read-v1）：
 * <ul>
 *   <li>{@code answers}：答卷号 → (引擎列名 → 值)。值可以为 {@code null}（未作答），
 *       因此内层用允许空值的不可变视图而不是 {@link Map#copyOf}；</li>
 *   <li>{@code extensions}：答卷号 → (副表题目代码 → 该题的结构化作答)。副表是行×列、
 *       行数逐份答卷不同，压不进扁平的 {@code answers}。</li>
 * </ul>
 */
public record AnswerBatch(Map<Long, Map<String, String>> answers,
        Map<Long, Map<String, ExtensionAnswer>> extensions) {

    /**
     * 一道副表题在一份答卷里的结构化作答（契约 question-extension-tables-v1）。
     *
     * @param structureVersion 写下这些单元格时的结构版本；{@code "0"} 表示无从考证，读端不得猜测列字典
     * @param valid            是否通过插件闸门；为 {@code false} 时 {@code rows} 必然是空的，
     *                         但这<b>不等于</b>作答者没填——引擎答卷列里还留着不可信的原文
     * @param rows             行序即下标；单元格值一律是字符串，数据库里的 {@code NULL} 为空串
     */
    public record ExtensionAnswer(String structureVersion, boolean valid, List<Map<String, String>> rows) {

        public ExtensionAnswer {
            Objects.requireNonNull(structureVersion, "structureVersion");
            rows = rows.stream()
                    .map(row -> Collections.unmodifiableMap(new LinkedHashMap<>(row)))
                    .toList();
        }
    }

    public AnswerBatch {
        Map<Long, Map<String, String>> copy = new LinkedHashMap<>();
        answers.forEach((id, values) -> copy.put(id, Collections.unmodifiableMap(new LinkedHashMap<>(values))));
        answers = Collections.unmodifiableMap(copy);
        Map<Long, Map<String, ExtensionAnswer>> sideTables = new LinkedHashMap<>();
        extensions.forEach((id, questions) -> sideTables.put(id, Map.copyOf(questions)));
        extensions = Collections.unmodifiableMap(sideTables);
    }

    /** 只有引擎答卷列、没有副表作答。 */
    public AnswerBatch(Map<Long, Map<String, String>> answers) {
        this(answers, Map.of());
    }

    public static AnswerBatch empty() {
        return new AnswerBatch(Map.of(), Map.of());
    }

    /** 某份答卷某道副表题的作答；没有就是 {@code null}（这道题这次作答没有被插件处理过）。 */
    public ExtensionAnswer extension(long responseId, String questionCode) {
        return extensions.getOrDefault(responseId, Map.of()).get(questionCode);
    }
}
