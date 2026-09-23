package cn.mjy.platform.survey;

import cn.mjy.platform.survey.QuestionTextParser.Parsed;
import cn.mjy.platform.survey.QuestionTextParser.QuestionType;
import cn.mjy.platform.survey.SurveyImportPreview.PreviewOption;
import cn.mjy.platform.survey.SurveyImportPreview.PreviewQuestion;
import java.util.ArrayList;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Locale;
import java.util.Set;
import java.util.UUID;
import java.util.regex.Matcher;
import java.util.regex.Pattern;
import org.springframework.stereotype.Component;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.node.ArrayNode;
import tools.jackson.databind.node.ObjectNode;

/**
 * 文本导入的第二步：把 {@link QuestionTextParser} 的解析结果配上题目代码做成预览，
 * 以及按作者的取舍把选中的题目并进草稿定义（WP-01 01.1）。
 *
 * <p>预览与落库读同一份原文、走同一条解析与编号，所以"预览里看到的题号、顺序、代码"
 * 就是落库后的结果；中途有人改了草稿时乐观锁会挡下这次导入（409），不会用过时的编号写进去。
 */
@Component
class QuestionImports {

    /** 没有指定分组时新建的分组标题。 */
    static final String DEFAULT_GROUP_TITLE = "导入的题目";
    /** 自动分配的题目代码形如 Q1、Q2；只按这个形状避让，其它形状的代码不可能与之相同。 */
    private static final Pattern GENERATED_CODE = Pattern.compile("^Q(\\d{1,6})$");
    /** 排序题的选项在引擎里是子题（见 question-type-map R02-15）。 */
    private static final String RANKING = "R";

    SurveyImportPreview preview(JsonNode draft, String text) {
        QuestionTextParser.Batch batch = QuestionTextParser.parse(text);
        int nextCode = nextCodeNumber(draft);
        List<PreviewQuestion> questions = new ArrayList<>(batch.questions().size());
        for (int index = 0; index < batch.questions().size(); index++) {
            Parsed parsed = batch.questions().get(index);
            questions.add(toPreview(index, "Q" + (nextCode + index), parsed));
        }
        return new SurveyImportPreview(batch.lineCount(), questions, batch.problems());
    }

    /**
     * 按 accept 里的序号把预览中的题目并进 {@code draft} 的副本并返回。
     * 序号越界、选中不可导入的题目、指定的分组不存在，都在这里拒绝，绝不"挑能用的写进去"。
     */
    ObjectNode merge(ObjectNode draft, String text, List<Integer> accept, String groupUuid) {
        SurveyImportPreview preview = preview(draft, text);
        List<PreviewQuestion> accepted = select(preview, accept);
        ObjectNode merged = draft.deepCopy();
        ArrayNode target = targetQuestions(merged, groupUuid);
        for (PreviewQuestion question : accepted) {
            appendDefinition(target, question);
        }
        return merged;
    }

    private static List<PreviewQuestion> select(SurveyImportPreview preview, List<Integer> accept) {
        if (accept == null || accept.isEmpty()) {
            throw new InvalidSurveyRequestException("accept must list at least one previewed question");
        }
        Set<Integer> unique = new LinkedHashSet<>(accept);
        List<PreviewQuestion> accepted = new ArrayList<>(unique.size());
        List<ImportProblem> blocking = new ArrayList<>();
        for (Integer index : unique) {
            if (index == null || index < 0 || index >= preview.questions().size()) {
                throw new InvalidSurveyRequestException("accept contains index " + index
                        + ", but the preview has " + preview.questions().size() + " questions");
            }
            PreviewQuestion question = preview.questions().get(index);
            if (question.importable()) {
                accepted.add(question);
            } else {
                blocking.addAll(question.problems());
            }
        }
        if (!blocking.isEmpty()) {
            throw new InvalidImportException(blocking);
        }
        // 按预览顺序落库，与作者选择的先后无关。
        return accepted.stream().sorted((a, b) -> Integer.compare(a.index(), b.index())).toList();
    }

    /** 指定分组时追加到该分组末尾；没指定时新建一个分组。 */
    private static ArrayNode targetQuestions(ObjectNode draft, String groupUuid) {
        JsonNode groups = draft.get("groups");
        if (!(groups instanceof ArrayNode array)) {
            throw new InvalidSurveyRequestException("draft definition has no groups array");
        }
        if (groupUuid == null || groupUuid.isBlank()) {
            ObjectNode group = array.addObject();
            group.put("uuid", UUID.randomUUID().toString());
            group.put("title", DEFAULT_GROUP_TITLE);
            return group.putArray("questions");
        }
        for (JsonNode group : array) {
            if (group.isObject() && groupUuid.equals(text(group.get("uuid")))) {
                JsonNode questions = group.get("questions");
                return questions instanceof ArrayNode existing ? existing : ((ObjectNode) group).putArray("questions");
            }
        }
        throw new InvalidSurveyRequestException("group " + groupUuid + " is not in this draft");
    }

    private static PreviewQuestion toPreview(int index, String code, Parsed parsed) {
        QuestionType type = parsed.type();
        List<PreviewOption> options = new ArrayList<>(parsed.options().size());
        for (int i = 0; i < parsed.options().size(); i++) {
            options.add(new PreviewOption(optionCode(type, i), parsed.options().get(i)));
        }
        return new PreviewQuestion(index, parsed.line(), code, type.letter(), type.name(), parsed.typeInferred(),
                parsed.text(), parsed.mandatory(), options, parsed.problems().isEmpty(), parsed.problems());
    }

    private static String optionCode(QuestionType type, int ordinal) {
        return RANKING.equals(type.letter())
                ? String.format(Locale.ROOT, "SQ%03d", ordinal + 1)
                : "A" + (ordinal + 1);
    }

    /** 按定义格式追加一道题；排序题的选项写成子题，其余写成答案选项。 */
    private static void appendDefinition(ArrayNode target, PreviewQuestion question) {
        boolean ranking = RANKING.equals(question.type());
        ObjectNode node = target.addObject();
        node.put("uuid", UUID.randomUUID().toString());
        node.put("code", question.code());
        node.put("type", question.type());
        node.put("text", question.text());
        if (question.mandatory()) {
            node.put("mandatory", true);
        }
        if (question.options().isEmpty()) {
            return;
        }
        ArrayNode items = node.putArray(ranking ? "subquestions" : "answers");
        for (PreviewOption option : question.options()) {
            ObjectNode item = items.addObject();
            if (ranking) {
                item.put("uuid", UUID.randomUUID().toString());
            }
            item.put("code", option.code());
            item.put("text", option.text());
        }
    }

    /** 草稿里已用过的 Q<数字> 代码的最大值加一，保证导入的代码不与既有代码撞车。 */
    private static int nextCodeNumber(JsonNode draft) {
        int next = 1;
        JsonNode groups = draft.get("groups");
        if (groups == null || !groups.isArray()) {
            return next;
        }
        for (JsonNode group : groups) {
            JsonNode questions = group.get("questions");
            if (questions == null || !questions.isArray()) {
                continue;
            }
            for (JsonNode question : questions) {
                Matcher matcher = GENERATED_CODE.matcher(String.valueOf(text(question.get("code"))));
                if (matcher.matches()) {
                    next = Math.max(next, Integer.parseInt(matcher.group(1)) + 1);
                }
            }
        }
        return next;
    }

    private static String text(JsonNode node) {
        return node == null || !node.isString() ? null : node.asString();
    }
}
