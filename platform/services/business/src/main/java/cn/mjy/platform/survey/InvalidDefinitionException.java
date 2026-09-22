package cn.mjy.platform.survey;

import java.util.List;

/** 问卷定义未通过服务端形状校验（422）。一次列出全部问题，而不是只报第一个。 */
public class InvalidDefinitionException extends RuntimeException {

    private final List<String> problems;

    public InvalidDefinitionException(List<String> problems) {
        super("invalid survey definition: " + String.join("; ", problems));
        this.problems = List.copyOf(problems);
    }

    public List<String> problems() {
        return problems;
    }
}
