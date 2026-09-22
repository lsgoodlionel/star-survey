package cn.mjy.platform.response;

import cn.mjy.platform.response.FieldDictionary.FieldEntry;
import cn.mjy.platform.response.FieldDictionary.OptionLabel;
import cn.mjy.platform.survey.PublishedVersionView.QuestionFieldView;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import tools.jackson.databind.JsonNode;

/**
 * 从已发布版本的定义快照与绑定映射推出字段字典。纯函数，不访问数据库与引擎。
 *
 * <p>aid 的形状与网关 {@code qtypes.expected_rows} 一致：单列题为空串，子题列为子题代码，
 * 评论列为"子题代码＋comment"，"其他"为 {@code other} / {@code othercomment}。
 * 快照里找不到对应题目时（理论上不会发生）退回用题目代码作标签，且不当作敏感——
 * 敏感标记只来自定义本身，不做猜测。
 */
final class FieldDictionaryBuilder {

    static final String OTHER = "other";
    static final String OTHER_COMMENT = "othercomment";
    static final String COMMENT_SUFFIX = "comment";
    static final String OTHER_LABEL = "其他";
    static final String COMMENT_LABEL = "（评论）";

    private FieldDictionaryBuilder() {
    }

    static List<FieldEntry> fields(JsonNode definition, List<QuestionFieldView> binding) {
        Map<String, JsonNode> questions = questionsByUuid(definition);
        List<FieldEntry> entries = new ArrayList<>();
        for (QuestionFieldView field : binding) {
            JsonNode question = questions.get(field.questionUuid().toString());
            entries.add(entry(field, Optional.ofNullable(question)));
        }
        return List.copyOf(entries);
    }

    private static FieldEntry entry(QuestionFieldView field, Optional<JsonNode> question) {
        String label = question.map(q -> label(q, field.aid())).orElse(field.code());
        boolean sensitive = question.map(q -> q.path("sensitive").asBoolean(false)).orElse(false);
        List<OptionLabel> options = question.map(q -> options(q, field)).orElse(List.of());
        return new FieldEntry(field.fieldname(), field.questionUuid(), field.code(), field.type(), field.aid(),
                field.scale(), label, sensitive, options);
    }

    private static String label(JsonNode question, String aid) {
        String text = question.path("text").asString(question.path("code").asString(""));
        if (aid.isEmpty()) {
            return text;
        }
        if (OTHER.equals(aid)) {
            return text + " [" + OTHER_LABEL + "]";
        }
        if (OTHER_COMMENT.equals(aid)) {
            return text + " [" + OTHER_LABEL + "]" + COMMENT_LABEL;
        }
        Optional<String> sub = subquestionText(question, aid);
        if (sub.isPresent()) {
            return text + " [" + sub.get() + "]";
        }
        if (aid.endsWith(COMMENT_SUFFIX)) {
            String base = aid.substring(0, aid.length() - COMMENT_SUFFIX.length());
            return text + " [" + subquestionText(question, base).orElse(base) + "]" + COMMENT_LABEL;
        }
        return text + " [" + aid + "]";
    }

    /** 选项只属于"选答案"的列：单列题本身或某个子题（按尺度）；"其他"与评论列是自由文本。 */
    private static List<OptionLabel> options(JsonNode question, QuestionFieldView field) {
        String aid = field.aid();
        boolean choiceColumn = aid.isEmpty() || subquestionText(question, aid).isPresent();
        if (!choiceColumn) {
            return List.of();
        }
        List<OptionLabel> options = new ArrayList<>();
        for (JsonNode answer : question.path("answers")) {
            if (answer.path("scale").asInt(0) == field.scale()) {
                options.add(new OptionLabel(answer.path("code").asString(""), answer.path("text").asString("")));
            }
        }
        return List.copyOf(options);
    }

    private static Optional<String> subquestionText(JsonNode question, String code) {
        for (JsonNode sub : question.path("subquestions")) {
            if (code.equals(sub.path("code").asString(null))) {
                return Optional.of(sub.path("text").asString(code));
            }
        }
        return Optional.empty();
    }

    private static Map<String, JsonNode> questionsByUuid(JsonNode definition) {
        Map<String, JsonNode> byUuid = new HashMap<>();
        for (JsonNode group : definition.path("groups")) {
            for (JsonNode question : group.path("questions")) {
                String uuid = question.path("uuid").asString(null);
                if (uuid != null) {
                    byUuid.put(uuid.toLowerCase(java.util.Locale.ROOT), question);
                }
            }
        }
        return byUuid;
    }
}
