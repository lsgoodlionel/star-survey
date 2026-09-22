package cn.mjy.platform.survey;

import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.HashSet;
import java.util.List;
import java.util.Set;
import java.util.UUID;
import org.springframework.stereotype.Component;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.json.JsonMapper;
import tools.jackson.databind.node.ObjectNode;

/**
 * 问卷定义的服务端形状校验与规范化。定义沿用发布网关现有格式
 * （{@code platform/tests/fixtures/surveys/publish-gateway.json}，网关侧 {@code pubgw/model.py}）。
 *
 * <p>这里只守最小形状——网关解析时必需的字段与类型、题目 UUID/代码不重复——让明显坏掉的定义在保存时就被拒绝；
 * 题型、代码合法性等语义规则由网关的 validate 阶段负责（以引擎规则为准，平台不复制一份）。
 *
 * <p>定义里的 {@code uuid} 一律由服务端写成问卷的公开 UUID，客户端给的值被忽略。
 */
@Component
public class SurveyDefinitions {

    /** 网关请求体上限 1 MiB（契约），给信封（requestId、实例标识）留出余量。 */
    static final int MAX_DEFINITION_BYTES = 1024 * 1024 - 4096;
    /** 网关支持的定义版本：1 为基础格式，2 在其上加入逻辑 DSL（契约 survey-logic-dsl-v1）。 */
    static final Set<Integer> DEFINITION_VERSIONS = Set.of(1, 2);

    private final JsonMapper json;

    public SurveyDefinitions(JsonMapper json) {
        this.json = json;
    }

    /** 校验并返回一份写入了问卷公开 UUID 的副本；不通过时抛 {@link InvalidDefinitionException}。 */
    public ObjectNode normalize(JsonNode raw, UUID surveyId) {
        if (raw == null || !raw.isObject()) {
            throw new InvalidDefinitionException(List.of("definition must be a JSON object"));
        }
        ObjectNode definition = (ObjectNode) raw.deepCopy();
        definition.put("uuid", surveyId.toString());
        List<String> problems = new ArrayList<>();
        checkTopLevel(definition, problems);
        checkGroups(definition.get("groups"), problems);
        if (problems.isEmpty() && serialize(definition).getBytes(StandardCharsets.UTF_8).length > MAX_DEFINITION_BYTES) {
            problems.add("definition exceeds " + MAX_DEFINITION_BYTES + " bytes");
        }
        if (!problems.isEmpty()) {
            throw new InvalidDefinitionException(problems);
        }
        return definition;
    }

    public String title(JsonNode definition) {
        return definition.get("title").asString();
    }

    public String serialize(JsonNode definition) {
        return json.writeValueAsString(definition);
    }

    public JsonNode parse(String text) {
        return json.readTree(text);
    }

    private static void checkTopLevel(JsonNode definition, List<String> problems) {
        JsonNode version = definition.get("definitionVersion");
        if (version == null || !version.isIntegralNumber() || !DEFINITION_VERSIONS.contains(version.intValue())) {
            problems.add("definitionVersion must be 1 or 2");
        }
        requireText(definition, "title", "definition", problems);
        requireText(definition, "language", "definition", problems);
        JsonNode settings = definition.get("settings");
        if (settings != null && !settings.isNull()) {
            checkStringMap(settings, "settings", problems);
        }
    }

    private static void checkGroups(JsonNode groups, List<String> problems) {
        if (groups == null || !groups.isArray() || groups.isEmpty()) {
            problems.add("groups must be a non-empty array");
            return;
        }
        Set<String> questionUuids = new HashSet<>();
        Set<String> questionCodes = new HashSet<>();
        int questionCount = 0;
        for (int g = 0; g < groups.size(); g++) {
            String where = "groups[" + g + "]";
            JsonNode group = groups.get(g);
            if (!group.isObject()) {
                problems.add(where + " must be an object");
                continue;
            }
            requireUuid(group, where, problems);
            requireText(group, "title", where, problems);
            JsonNode questions = group.get("questions");
            if (questions == null || !questions.isArray()) {
                problems.add(where + ".questions must be an array");
                continue;
            }
            for (int q = 0; q < questions.size(); q++) {
                checkQuestion(questions.get(q), where + ".questions[" + q + "]", questionUuids, questionCodes, problems);
                questionCount++;
            }
        }
        if (questionCount == 0) {
            problems.add("the definition has no questions");
        }
    }

    private static void checkQuestion(JsonNode question, String where, Set<String> uuids, Set<String> codes,
            List<String> problems) {
        if (!question.isObject()) {
            problems.add(where + " must be an object");
            return;
        }
        if (requireUuid(question, where, problems) && !uuids.add(question.get("uuid").asString())) {
            problems.add(where + ": duplicate question uuid " + question.get("uuid").asString());
        }
        if (requireText(question, "code", where, problems) && !codes.add(question.get("code").asString())) {
            problems.add(where + ": duplicate question code " + question.get("code").asString());
        }
        requireText(question, "type", where, problems);
        JsonNode text = question.get("text");
        if (text == null || !text.isString()) {
            problems.add(where + ".text must be a string");
        }
    }

    private static boolean requireText(JsonNode node, String field, String where, List<String> problems) {
        JsonNode value = node.get(field);
        if (value == null || !value.isString() || value.asString().isBlank()) {
            problems.add(where + "." + field + " must be a non-blank string");
            return false;
        }
        return true;
    }

    private static boolean requireUuid(JsonNode node, String where, List<String> problems) {
        JsonNode value = node.get("uuid");
        if (value == null || !value.isString() || !isUuid(value.asString())) {
            problems.add(where + ".uuid must be a UUID");
            return false;
        }
        return true;
    }

    private static void checkStringMap(JsonNode map, String where, List<String> problems) {
        if (!map.isObject()) {
            problems.add(where + " must be an object");
            return;
        }
        for (String name : map.propertyNames()) {
            if (!map.get(name).isString()) {
                problems.add(where + "." + name + " must be a string");
            }
        }
    }

    private static boolean isUuid(String value) {
        try {
            return UUID.fromString(value).toString().equalsIgnoreCase(value);
        } catch (IllegalArgumentException e) {
            return false;
        }
    }
}
