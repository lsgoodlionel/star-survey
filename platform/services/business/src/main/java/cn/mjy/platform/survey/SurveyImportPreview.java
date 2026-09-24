package cn.mjy.platform.survey;

import java.util.List;

/**
 * 批量文本导入的预览（WP-01 01.1，需求 R01-02）：落库之前把解析结果全部摊给作者看。
 *
 * @param lineCount 原文行数
 * @param questions 解析出的候选题目，按原文顺序；{@code index} 就是确认导入时用的取舍序号
 * @param problems  不属于任何题目的坏行（认不出来的行），带行号；坏行不影响其余题目
 */
public record SurveyImportPreview(int lineCount, List<PreviewQuestion> questions, List<ImportProblem> problems) {

    public SurveyImportPreview {
        questions = List.copyOf(questions);
        problems = List.copyOf(problems);
    }

    /**
     * 一道候选题目。
     *
     * @param index        取舍序号（0 起），确认导入时按它挑选
     * @param line         题干在原文里的行号
     * @param code         将要写入定义的题目代码（已避开草稿里已有的代码）
     * @param type         引擎题型字母
     * @param typeName     题型中文名
     * @param typeInferred 原文没写题型、由选项有无推断而来
     * @param importable   没有阻断性问题，可以被选中导入
     * @param problems     本题自身的问题
     */
    public record PreviewQuestion(int index, int line, String code, String type, String typeName,
            boolean typeInferred, String text, boolean mandatory, List<PreviewOption> options,
            boolean importable, List<ImportProblem> problems) {

        public PreviewQuestion {
            options = List.copyOf(options);
            problems = List.copyOf(problems);
        }
    }

    /** 一个选项：code 由位置生成（A1、A2…），text 是原文去掉序号标记后的内容。 */
    public record PreviewOption(String code, String text) {
    }
}
