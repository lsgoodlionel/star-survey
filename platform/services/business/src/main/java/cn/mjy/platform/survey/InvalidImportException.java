package cn.mjy.platform.survey;

import java.util.List;

/** 确认导入时，被选中的题目里仍有阻断性问题（422）。problems 原样带回，供作者逐条定位。 */
public class InvalidImportException extends RuntimeException {

    private final transient List<ImportProblem> problems;

    public InvalidImportException(List<ImportProblem> problems) {
        super("selected questions cannot be imported");
        this.problems = List.copyOf(problems);
    }

    public List<ImportProblem> problems() {
        return problems;
    }
}
