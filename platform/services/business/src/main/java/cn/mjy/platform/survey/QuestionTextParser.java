package cn.mjy.platform.survey;

import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/**
 * 把"批量录入"的纯文本切成题目（WP-01 01.1，需求 R01-02）。纯函数，不碰数据库、不依赖 Spring。
 *
 * <p>约定的写法：
 * <pre>
 * 1. 您的性别？[单选]
 * A. 男
 * B. 女
 *
 * 2. 平时用到哪些功能？【多选，必答】
 * A. 搜索
 * B. 收藏
 * </pre>
 *
 * <ul>
 *   <li><b>题干行</b>以数字加 {@code . 、 ) ）} 开头；方括号或中文方括号里是标签，用逗号、顿号或空格分隔，
 *       其中一个是题型（{@code 单选 / 多选 / 填空 / 多行文本 / 评分 / 排序 / 日期 / 说明}），
 *       另有 {@code 必答} 一类的标记。没写题型时按"有没有选项"推断，并在预览里标明是推断的。</li>
 *   <li><b>选项行</b>以单个字母加 {@code . 、 ) ）} 开头，或以 {@code - * •} 开头。
 *       数字开头的一律当题干行——两种标记必须分开，否则无从区分。</li>
 *   <li>题干行之后、第一个选项之前的普通行是<b>题干续行</b>（题干本来就可能换行）。</li>
 *   <li>其余认不出来的行记一条 {@code unrecognized_line}，<b>不中断整批</b>，也不静默丢弃。</li>
 * </ul>
 *
 * <p>解析只认形状，不看草稿：题目代码、UUID 由 {@link QuestionImports} 结合草稿再分配。
 */
final class QuestionTextParser {

    /** 原文上限：与网关 1 MiB 的定义上限相称，留足余量。 */
    static final int MAX_TEXT_BYTES = 256 * 1024;
    static final int MAX_LINES = 5_000;
    static final int MAX_QUESTIONS = 500;
    static final int MAX_OPTIONS = 200;
    static final int MAX_QUESTION_TEXT = 2_000;
    static final int MAX_OPTION_TEXT = 500;
    /** 需要选项的题型至少要有几个选项。 */
    private static final int MIN_OPTIONS = 2;

    private static final Pattern QUESTION_START = Pattern.compile("^\\d{1,4}\\s*[.、)）:：]\\s*(.*)$");
    private static final Pattern OPTION_START =
            Pattern.compile("^(?:[A-Za-z]\\s*[.、)）]|[-*•·]\\s)\\s*(.*)$");
    private static final Pattern TAG_BLOCK = Pattern.compile("[\\[【]([^\\]】]*)[\\]】]\\s*$");
    private static final Pattern TAG_SEPARATOR = Pattern.compile("[,，、/\\s]+");
    private static final List<String> MANDATORY_TAGS = List.of("必答", "必填", "required");

    /** 一种题型：引擎题型字母、中文名、是否吃选项。 */
    record QuestionType(String letter, String name, boolean takesOptions) {
    }

    private static final QuestionType SINGLE = new QuestionType("L", "单选", true);
    private static final QuestionType TEXT = new QuestionType("S", "填空", false);
    private static final Map<String, QuestionType> TYPES_BY_TAG = typesByTag();

    private static Map<String, QuestionType> typesByTag() {
        QuestionType multi = new QuestionType("M", "多选", true);
        QuestionType longText = new QuestionType("T", "多行文本", false);
        QuestionType rating = new QuestionType("5", "评分", false);
        QuestionType ranking = new QuestionType("R", "排序", true);
        QuestionType date = new QuestionType("D", "日期", false);
        QuestionType note = new QuestionType("X", "说明", false);
        Map<String, QuestionType> tags = new LinkedHashMap<>();
        for (String tag : List.of("单选", "单项选择", "radio")) {
            tags.put(tag, SINGLE);
        }
        for (String tag : List.of("多选", "多项选择", "checkbox")) {
            tags.put(tag, multi);
        }
        for (String tag : List.of("填空", "单行文本", "文本", "text")) {
            tags.put(tag, TEXT);
        }
        for (String tag : List.of("多行文本", "长文本", "简答", "textarea")) {
            tags.put(tag, longText);
        }
        for (String tag : List.of("评分", "量表", "rating")) {
            tags.put(tag, rating);
        }
        for (String tag : List.of("排序", "ranking")) {
            tags.put(tag, ranking);
        }
        for (String tag : List.of("日期", "date")) {
            tags.put(tag, date);
        }
        for (String tag : List.of("说明", "说明文字", "note")) {
            tags.put(tag, note);
        }
        return Map.copyOf(tags);
    }

    private QuestionTextParser() {
    }

    /** 解析结果：题目按原文顺序，problems 是不属于任何题目的坏行。 */
    record Batch(int lineCount, List<Parsed> questions, List<ImportProblem> problems) {
    }

    /** 一道解析出来的题目；problems 非空即为阻断性问题（不可导入）。 */
    record Parsed(int line, String text, QuestionType type, boolean typeInferred, boolean mandatory,
            List<String> options, List<ImportProblem> problems) {
    }

    static Batch parse(String raw) {
        String text = requireText(raw);
        List<String> lines = List.of(text.replace("\r\n", "\n").replace('\r', '\n').split("\n", -1));
        if (lines.size() > MAX_LINES) {
            throw new InvalidSurveyRequestException("text has " + lines.size() + " lines, at most " + MAX_LINES);
        }
        Collector collector = new Collector();
        for (int i = 0; i < lines.size(); i++) {
            collector.accept(i + 1, lines.get(i).strip());
        }
        collector.flush();
        if (collector.questions.size() > MAX_QUESTIONS) {
            throw new InvalidSurveyRequestException(
                    "text has " + collector.questions.size() + " questions, at most " + MAX_QUESTIONS);
        }
        return new Batch(lines.size(), List.copyOf(collector.questions), List.copyOf(collector.problems));
    }

    private static String requireText(String raw) {
        if (raw == null || raw.isBlank()) {
            throw new InvalidSurveyRequestException("text is required");
        }
        if (raw.getBytes(StandardCharsets.UTF_8).length > MAX_TEXT_BYTES) {
            throw new InvalidSurveyRequestException("text exceeds " + MAX_TEXT_BYTES + " bytes");
        }
        return raw;
    }

    /** 逐行归集：当前题目积累题干续行与选项，遇到下一个题干行就结算上一题。 */
    private static final class Collector {

        private final List<Parsed> questions = new ArrayList<>();
        private final List<ImportProblem> problems = new ArrayList<>();
        private Header header;
        private final List<String> stem = new ArrayList<>();
        private final List<String> options = new ArrayList<>();

        void accept(int lineNo, String line) {
            if (line.isEmpty()) {
                return;
            }
            Matcher question = QUESTION_START.matcher(line);
            if (question.matches()) {
                flush();
                header = Header.of(lineNo, question.group(1).strip());
                return;
            }
            Matcher option = OPTION_START.matcher(line);
            if (option.matches() && header != null) {
                options.add(option.group(1).strip());
                return;
            }
            if (header != null && options.isEmpty()) {
                stem.add(line);
                return;
            }
            problems.add(new ImportProblem(lineNo, "unrecognized_line",
                    "这一行既不是题干（数字开头）也不是选项（字母或 - 开头），已跳过"));
        }

        void flush() {
            if (header == null) {
                return;
            }
            questions.add(build(header, join(header.text(), stem), List.copyOf(options)));
            header = null;
            stem.clear();
            options.clear();
        }

        private static String join(String first, List<String> rest) {
            return rest.isEmpty() ? first
                    : (first.isEmpty() ? String.join("\n", rest) : first + "\n" + String.join("\n", rest));
        }
    }

    /** 题干行拆出来的部分：正文、标签里认出的题型与必答标记，以及标签本身的问题。 */
    private record Header(int line, String text, QuestionType type, boolean mandatory,
            List<ImportProblem> problems) {

        static Header of(int line, String rest) {
            Matcher tags = TAG_BLOCK.matcher(rest);
            if (!tags.find()) {
                return new Header(line, rest, null, false, List.of());
            }
            String body = rest.substring(0, tags.start()).strip();
            List<ImportProblem> problems = new ArrayList<>();
            QuestionType type = null;
            boolean mandatory = false;
            for (String tag : TAG_SEPARATOR.split(tags.group(1).strip())) {
                if (tag.isEmpty()) {
                    continue;
                }
                QuestionType found = TYPES_BY_TAG.get(tag.toLowerCase(Locale.ROOT));
                if (found != null && type != null && !found.equals(type)) {
                    problems.add(new ImportProblem(line, "ambiguous_question_type",
                            "一道题写了多个题型：" + type.name() + "、" + found.name()));
                } else if (found != null) {
                    type = found;
                } else if (MANDATORY_TAGS.contains(tag.toLowerCase(Locale.ROOT))) {
                    mandatory = true;
                } else {
                    problems.add(new ImportProblem(line, "unknown_tag", "认不出的标签：" + tag));
                }
            }
            return new Header(line, body, type, mandatory, problems);
        }
    }

    private static Parsed build(Header header, String text, List<String> options) {
        List<ImportProblem> problems = new ArrayList<>(header.problems());
        boolean inferred = header.type() == null;
        QuestionType type = inferred ? (options.isEmpty() ? TEXT : SINGLE) : header.type();
        checkText(header.line(), text, problems);
        checkOptions(header.line(), type, options, problems);
        return new Parsed(header.line(), text, type, inferred, header.mandatory(), options, List.copyOf(problems));
    }

    private static void checkText(int line, String text, List<ImportProblem> problems) {
        if (text.isBlank()) {
            problems.add(new ImportProblem(line, "blank_question_text", "题干是空的"));
        } else if (text.length() > MAX_QUESTION_TEXT) {
            problems.add(new ImportProblem(line, "question_text_too_long",
                    "题干超过 " + MAX_QUESTION_TEXT + " 字"));
        }
    }

    private static void checkOptions(int line, QuestionType type, List<String> options,
            List<ImportProblem> problems) {
        if (!type.takesOptions()) {
            if (!options.isEmpty()) {
                problems.add(new ImportProblem(line, "options_not_allowed",
                        type.name() + "题不能带选项，请改题型或删掉选项"));
            }
            return;
        }
        if (options.size() < MIN_OPTIONS) {
            problems.add(new ImportProblem(line, "too_few_options",
                    type.name() + "题至少要有 " + MIN_OPTIONS + " 个选项，这里只有 " + options.size() + " 个"));
        }
        if (options.size() > MAX_OPTIONS) {
            problems.add(new ImportProblem(line, "too_many_options",
                    "选项超过 " + MAX_OPTIONS + " 个"));
        }
        checkOptionTexts(line, options, problems);
    }

    private static void checkOptionTexts(int line, List<String> options, List<ImportProblem> problems) {
        List<String> seen = new ArrayList<>(options.size());
        for (String option : options) {
            if (option.isBlank()) {
                problems.add(new ImportProblem(line, "blank_option", "有一个选项是空的"));
            } else if (option.length() > MAX_OPTION_TEXT) {
                problems.add(new ImportProblem(line, "option_text_too_long",
                        "选项超过 " + MAX_OPTION_TEXT + " 字"));
            } else if (seen.contains(option)) {
                problems.add(new ImportProblem(line, "duplicate_option", "选项重复：" + option));
            }
            seen.add(option);
        }
    }
}
