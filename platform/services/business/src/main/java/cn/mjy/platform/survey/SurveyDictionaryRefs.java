package cn.mjy.platform.survey;

import java.util.ArrayList;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Set;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.node.ArrayNode;
import tools.jackson.databind.node.ObjectNode;

/**
 * 定义里的字典引用：谁引用了哪本字典、发布时把版本写回哪里、草稿里哪些键一概不算数。
 *
 * <p>引用写在题目的 {@code themeOptions} 上：
 * <pre>
 * {"theme": "mjy-cascading-select",
 *  "themeOptions": {"structureVersion": "r1", "dictionary": "cn-admin-divisions", "levels": [...]}}
 * </pre>
 * 作者只写<b>哪本字典</b>，不写哪一版——版本由平台在发布时固化（ADR 0019 决定 2）。
 */
final class SurveyDictionaryRefs {

    /** 多级下拉（R02-03）的题型主题名，与发布网关的注册表一致。 */
    static final String CASCADING_THEME = "mjy-cascading-select";

    static final String THEME_OPTIONS = "themeOptions";
    static final String DICTIONARY = "dictionary";
    static final String VERSION = "dictionaryVersion";
    static final String DIGEST = "dictionaryDigest";
    /** 定义顶层的字典快照，发布时物化；草稿里没有这个键。 */
    static final String DICTIONARIES = "dictionaries";

    private SurveyDictionaryRefs() {
    }

    /** 本定义引用到的字典代码，按出现顺序去重。 */
    static List<String> codesIn(JsonNode definition) {
        Set<String> codes = new LinkedHashSet<>();
        for (ObjectNode options : cascadingOptions(definition)) {
            JsonNode code = options.get(DICTIONARY);
            if (code != null && code.isString() && !code.asString().isBlank()) {
                codes.add(code.asString());
            }
        }
        return List.copyOf(codes);
    }

    /**
     * 把草稿里客户端写的固化键摘掉。与 {@code participants} 同一个道理：客户端能自己钉版本，
     * 「已发布问卷引用的是确定的那一版」就由客户端说了算了。
     */
    static void stripPins(ObjectNode definition) {
        definition.remove(DICTIONARIES);
        for (ObjectNode options : cascadingOptions(definition)) {
            options.remove(VERSION);
            options.remove(DIGEST);
        }
    }

    /** 把固化结果写回每道题，并在顶层放一份节点快照（同一本字典只放一份）。 */
    static void applyPins(ObjectNode definition, List<SurveyDictionarySource.Pinned> pinned) {
        if (pinned.isEmpty()) {
            return;
        }
        for (ObjectNode options : cascadingOptions(definition)) {
            JsonNode code = options.get(DICTIONARY);
            if (code == null || !code.isString()) {
                continue;
            }
            pinned.stream().filter(one -> one.code().equals(code.asString())).findFirst()
                    .ifPresent(one -> {
                        options.put(VERSION, one.version());
                        options.put(DIGEST, one.digest());
                    });
        }
        ArrayNode dictionaries = definition.putArray(DICTIONARIES);
        for (SurveyDictionarySource.Pinned one : pinned) {
            ObjectNode entry = dictionaries.addObject();
            entry.put("code", one.code());
            entry.put("version", one.version());
            entry.put("digest", one.digest());
            // 紧凑三元组 [代码, 父代码, 标签]：几千个节点要塞进 1 MiB 的定义快照里，
            // 键名重复几千遍是纯浪费。父代码为空即根节点。
            ArrayNode nodes = entry.putArray("nodes");
            for (SurveyDictionarySource.Node node : one.nodes()) {
                ArrayNode triple = nodes.addArray();
                triple.add(node.code());
                triple.add(node.parentCode() == null ? "" : node.parentCode());
                triple.add(node.label());
            }
        }
    }

    /** 定义里所有多级下拉题的 {@code themeOptions} 对象（可改）。 */
    private static List<ObjectNode> cascadingOptions(JsonNode definition) {
        List<ObjectNode> found = new ArrayList<>();
        JsonNode groups = definition.get("groups");
        if (groups == null || !groups.isArray()) {
            return found;
        }
        for (JsonNode group : groups) {
            JsonNode questions = group.get("questions");
            if (questions == null || !questions.isArray()) {
                continue;
            }
            for (JsonNode question : questions) {
                JsonNode theme = question.get("theme");
                JsonNode options = question.get(THEME_OPTIONS);
                if (theme != null && theme.isString() && CASCADING_THEME.equals(theme.asString())
                        && options instanceof ObjectNode editable) {
                    found.add(editable);
                }
            }
        }
        return found;
    }
}
