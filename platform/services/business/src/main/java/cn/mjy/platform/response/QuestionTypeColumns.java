package cn.mjy.platform.response;

import cn.mjy.platform.response.FieldDictionary.OptionLabel;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import tools.jackson.databind.JsonNode;

/**
 * 各题型列的特殊含义（网关 {@code pubgw/qtypes.py}；映射表 platform/docs/p2/question-type-map.md）。
 * 字段字典先问这里，没有特殊规则的列再走通用规则。纯函数。
 *
 * <ul>
 *   <li>引擎内置刻度的题型（{@code 5 Y G A B C E}）不在定义里写选项，选项代码由引擎固定；</li>
 *   <li>多选（{@code M P}）的子题列选中为 {@code Y}；</li>
 *   <li>矩阵（{@code : ;}）的 aid 是"行代码_列代码"，行子题在尺度 0、列子题在尺度 1；</li>
 *   <li>排序（{@code R}）的主列是按名次排列的子题代码 JSON 数组，名次列 aid 为 1…n；</li>
 *   <li>单选带评论（{@code O}）的 {@code comment} 列、上传题（{@code |}）的 {@code filecount} 列是自由值；</li>
 *   <li>平台题型主题（{@code theme}）先于题型字母决定含义：副表题型的那一列是整块 JSON 信封，
 *       选项内嵌填空的评论列是主题配置的填空。</li>
 * </ul>
 */
final class QuestionTypeColumns {

    static final String COMMENT = "comment";
    static final String FILE_COUNT = "filecount";
    static final String COMMENT_LABEL = "（评论）";
    static final String FILE_COUNT_LABEL = "文件数";
    static final String CHECKED = "Y";
    static final String STRUCTURED_LABEL = "结构化作答";
    static final String INLINE_BLANK_THEME = "mjy-inline-blank";
    static final String DEFAULT_BLANK_LABEL = "补充";

    /**
     * 作答以 JSON 信封存进基础题型那一列、真正的结构在副表里的题型主题
     * （网关 {@code pubgw/questions/themes.py} 的 STRUCTURED_THEMES，
     * 契约 platform/contracts/question-extension-tables-v1.md）。
     * 这一列是整块 JSON，不是可枚举的取值。
     */
    private static final java.util.Set<String> STRUCTURED_THEMES =
            java.util.Set.of(
                    "mjy-repeating-table", "mjy-heatmap", "mjy-loop-rating",
                    "mjy-image-pk", "mjy-shelf", "mjy-text-highlight", "mjy-psych-trial",
                    "mjy-model-kano");

    private static final List<OptionLabel> FIVE = scale(5);
    private static final Map<String, List<OptionLabel>> BUILT_IN = Map.of(
            "5", FIVE,
            "A", FIVE,
            "B", scale(10),
            "Y", List.of(new OptionLabel("Y", "是"), new OptionLabel("N", "否")),
            "G", List.of(new OptionLabel("F", "女"), new OptionLabel("M", "男")),
            "C", List.of(new OptionLabel("Y", "是"), new OptionLabel("U", "不确定"), new OptionLabel("N", "否")),
            "E", List.of(new OptionLabel("I", "增加"), new OptionLabel("S", "不变"), new OptionLabel("D", "减少")));

    private QuestionTypeColumns() {
    }

    static Optional<String> label(JsonNode question, String type, String aid, String text) {
        String theme = question.path("theme").asString("");
        if (STRUCTURED_THEMES.contains(theme) && aid.isEmpty()) {
            return Optional.of(text + " [" + STRUCTURED_LABEL + "]");
        }
        if (INLINE_BLANK_THEME.equals(theme) && aid.endsWith(COMMENT)) {
            String base = aid.substring(0, aid.length() - COMMENT.length());
            String blank = question.path("themeOptions").path("blankLabel").asString(DEFAULT_BLANK_LABEL);
            return Optional.of(text + " [" + subquestionText(question, base, 0).orElse(base) + "]（" + blank + "）");
        }
        switch (type) {
            case ":", ";" -> {
                int split = aid.indexOf('_');
                if (split <= 0) {
                    return Optional.empty();
                }
                String row = aid.substring(0, split);
                String column = aid.substring(split + 1);
                return Optional.of(text + " [" + subquestionText(question, row, 0).orElse(row) + " / "
                        + subquestionText(question, column, 1).orElse(column) + "]");
            }
            case "R" -> {
                return aid.isEmpty() ? Optional.of(text) : Optional.of(text + " [第" + aid + "位]");
            }
            case "O" -> {
                return COMMENT.equals(aid) ? Optional.of(text + COMMENT_LABEL) : Optional.empty();
            }
            case "|" -> {
                return FILE_COUNT.equals(aid) ? Optional.of(text + " [" + FILE_COUNT_LABEL + "]") : Optional.empty();
            }
            default -> {
                return Optional.empty();
            }
        }
    }

    /** 该列可取的值；返回空表示交给通用规则（按定义里的 answers）。 */
    static Optional<List<OptionLabel>> options(JsonNode question, String type, String aid, boolean isChoiceColumn) {
        if (STRUCTURED_THEMES.contains(question.path("theme").asString(""))) {
            return Optional.of(List.of());
        }
        if (BUILT_IN.containsKey(type)) {
            return Optional.of(isChoiceColumn ? BUILT_IN.get(type) : List.of());
        }
        switch (type) {
            case "M", "P" -> {
                return Optional.of(isChoiceColumn && !aid.isEmpty() ? List.of(new OptionLabel(CHECKED, "已选")) : List.of());
            }
            case "R" -> {
                return Optional.of(subquestionOptions(question));
            }
            case ":", ";", "K", "Q", "|" -> {
                return Optional.of(List.of());
            }
            default -> {
                return Optional.empty();
            }
        }
    }

    private static List<OptionLabel> scale(int top) {
        List<OptionLabel> options = new ArrayList<>();
        for (int value = 1; value <= top; value++) {
            options.add(new OptionLabel(Integer.toString(value), Integer.toString(value)));
        }
        return List.copyOf(options);
    }

    private static List<OptionLabel> subquestionOptions(JsonNode question) {
        List<OptionLabel> options = new ArrayList<>();
        for (JsonNode sub : question.path("subquestions")) {
            String code = sub.path("code").asString("");
            options.add(new OptionLabel(code, sub.path("text").asString(code)));
        }
        return List.copyOf(options);
    }

    private static Optional<String> subquestionText(JsonNode question, String code, int scale) {
        for (JsonNode sub : question.path("subquestions")) {
            if (code.equals(sub.path("code").asString(null)) && sub.path("scale").asInt(0) == scale) {
                return Optional.of(sub.path("text").asString(code));
            }
        }
        return Optional.empty();
    }
}
