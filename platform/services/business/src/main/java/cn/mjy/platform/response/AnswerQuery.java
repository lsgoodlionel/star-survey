package cn.mjy.platform.response;

import java.util.List;
import java.util.Objects;

/**
 * 一次作答读取的请求（契约 response-read-v1）：要哪些答卷的哪些引擎答卷列，以及可选的扩展副表作答。
 *
 * <p>扩展副表要么不要，要么连代次一起给：代次是副表自然键的一段，缺了它网关一律 400
 * （契约 response-read-v1「扩展表作答」、question-extension-tables-v1 第一节）。这个约束在本记录里
 * 就地判，不等网关退回来才发现。
 *
 * @param generation         代次；只有要扩展副表时才非空
 * @param extensionQuestions 要读的副表题目代码（绑定记录里带 sideTable 的那些题）；空表示不读副表
 */
public record AnswerQuery(String engineInstanceId, long engineSid, List<Long> responseIds, List<String> fieldnames,
        String generation, List<String> extensionQuestions) {

    /** 契约允许一次点名的副表题目上限。 */
    public static final int MAX_EXTENSION_QUESTIONS = 50;

    public AnswerQuery {
        Objects.requireNonNull(engineInstanceId, "engineInstanceId");
        responseIds = List.copyOf(responseIds);
        fieldnames = List.copyOf(fieldnames);
        extensionQuestions = extensionQuestions == null ? List.of() : List.copyOf(extensionQuestions);
        if (!extensionQuestions.isEmpty() && (generation == null || generation.isEmpty())) {
            throw new IllegalArgumentException("extension questions must come with the generation they belong to");
        }
        if (extensionQuestions.size() > MAX_EXTENSION_QUESTIONS) {
            throw new IllegalArgumentException(
                    "at most " + MAX_EXTENSION_QUESTIONS + " extension questions can be read at once");
        }
    }

    /** 只读引擎答卷列，不读扩展副表。 */
    public static AnswerQuery of(String engineInstanceId, long engineSid, List<Long> responseIds,
            List<String> fieldnames) {
        return new AnswerQuery(engineInstanceId, engineSid, responseIds, fieldnames, null, List.of());
    }

    public boolean wantsExtensions() {
        return !extensionQuestions.isEmpty();
    }

    /** 一列作答都不要、副表也不要：不必问网关。 */
    public boolean isEmpty() {
        return fieldnames.isEmpty() && extensionQuestions.isEmpty();
    }
}
