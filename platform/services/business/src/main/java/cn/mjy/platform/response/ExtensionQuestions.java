package cn.mjy.platform.response;

import java.util.ArrayList;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Set;
import tools.jackson.databind.JsonNode;

/**
 * 从已发布版本的定义快照里认出「有副表的题」：作答以 JSON 信封存进基础题型那一列、
 * 真正的结构写在插件副表里的那些题型主题（契约 question-extension-tables-v1）。纯函数，不访问数据库与引擎。
 *
 * <p>网关不存绑定，副表题目由平台点名（契约 response-read-v1「扩展表作答」）。平台认的就是这里这一份
 * 主题名单，与 {@link QuestionTypeColumns} 判定「这一列是整块 JSON 信封」用的是同一个集合——
 * 两处分叉会让字段字典说这是信封、读取却不去要副表。
 */
final class ExtensionQuestions {

    private ExtensionQuestions() {
    }

    /**
     * 定义快照里带副表的题目代码，按定义里的出现顺序、去重。
     * 顺序固定是导出的前提：作业计划冻结这份名单，崩溃恢复重跑时要得到同样的请求与同样的行序。
     */
    static List<String> codes(JsonNode definition) {
        Set<String> seen = new LinkedHashSet<>();
        for (JsonNode group : definition.path("groups")) {
            for (JsonNode question : group.path("questions")) {
                if (!QuestionTypeColumns.STRUCTURED_THEMES.contains(question.path("theme").asString(""))) {
                    continue;
                }
                String code = question.path("code").asString("");
                if (!code.isEmpty()) {
                    seen.add(code);
                }
            }
        }
        return List.copyOf(new ArrayList<>(seen));
    }
}
