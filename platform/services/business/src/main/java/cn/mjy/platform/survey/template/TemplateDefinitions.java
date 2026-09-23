package cn.mjy.platform.survey.template;

import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.UUID;
import org.springframework.stereotype.Component;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.node.ArrayNode;
import tools.jackson.databind.node.ObjectNode;

/**
 * 把一份问卷定义变成模板定义，再从模板定义变出一份独立的问卷定义。
 *
 * <p><b>模板绝不携带生产数据</b>（R19-05 验收）。定义本身不含答卷，但它含三类必须剥掉的东西：
 *
 * <ol>
 *   <li>{@code participants}：参与者 token，等同于一批可以直接进入问卷的凭据；
 *   <li>{@code policy}：访问策略，里面有访问口令的 PBKDF2 哈希（{@code SurveyAccessPolicies} 在保存草稿时写入），
 *       还有只对那一次投放有意义的时间窗与 IP 规则；
 *   <li>{@code settings} 里的管理员与通知邮箱：是租户的联系人信息，不是问卷结构。
 * </ol>
 *
 * <p>另外，模板与它来源的问卷**不共享任何实体 id**：题组、题目、子题的 uuid 全部重新生成，
 * 每复制一次再生成一次。否则两份问卷会带着同一批 uuid，按 uuid 索引的东西（比如
 * {@code translations}）会张冠李戴。重新生成时同步重写 {@code translations} 里按 uuid 索引的键。
 *
 * <p>题目 <b>代码</b>（{@code code}）保持不变：逻辑 DSL 与表达式引用的是代码，不是 uuid。
 */
@Component
public class TemplateDefinitions {

    /** 模板里一律不出现的顶层键。{@code uuid} 会在复制时由 SurveyDefinitions 重写成新问卷的 id。 */
    static final Set<String> STRIPPED_KEYS = Set.of("participants", "policy", "uuid");

    /** 模板里一律不出现的设置：租户的联系人与通知地址。 */
    static final Set<String> STRIPPED_SETTINGS =
            Set.of("admin", "adminemail", "bounce_email", "emailresponseto", "emailnotificationto");

    private static final List<String> ENTITY_SECTIONS = List.of("groups", "questions", "subquestions");

    /** 问卷定义 → 模板定义：剥掉生产数据，再换一套实体 id。 */
    public ObjectNode toTemplate(JsonNode definition) {
        ObjectNode copy = require(definition);
        STRIPPED_KEYS.forEach(copy::remove);
        JsonNode settings = copy.get("settings");
        if (settings instanceof ObjectNode object) {
            STRIPPED_SETTINGS.forEach(object::remove);
        }
        return regenerateIds(copy);
    }

    /** 模板定义 → 新问卷的定义：再换一套实体 id，使每一份复制都彼此独立。 */
    public ObjectNode toSurvey(JsonNode templateDefinition) {
        return regenerateIds(require(templateDefinition));
    }

    private static ObjectNode require(JsonNode definition) {
        if (definition == null || !definition.isObject()) {
            throw new InvalidTemplateRequestException("definition must be a JSON object");
        }
        return (ObjectNode) definition.deepCopy();
    }

    // ------------------------------------------------------------ 实体 id

    private static ObjectNode regenerateIds(ObjectNode definition) {
        Map<String, String> renamed = new HashMap<>();
        for (JsonNode group : definition.path("groups")) {
            rename(group, renamed);
            for (JsonNode question : group.path("questions")) {
                rename(question, renamed);
                for (JsonNode subquestion : question.path("subquestions")) {
                    rename(subquestion, renamed);
                }
            }
        }
        remapTranslations(definition.get("translations"), renamed);
        return definition;
    }

    private static void rename(JsonNode entity, Map<String, String> renamed) {
        if (!(entity instanceof ObjectNode object)) {
            return;
        }
        JsonNode uuid = object.get("uuid");
        if (uuid == null || !uuid.isTextual()) {
            return;
        }
        String replacement = UUID.randomUUID().toString();
        renamed.put(uuid.asString(), replacement);
        object.put("uuid", replacement);
    }

    /**
     * {@code translations} 按 uuid 索引题组、题目、子题与选项（契约 survey-branding-v1 §3）。
     * 实体换了 id，这些键也要跟着换，否则译文会指向不存在的实体，发布时被网关判为 422。
     */
    private static void remapTranslations(JsonNode translations, Map<String, String> renamed) {
        if (!(translations instanceof ObjectNode languages)) {
            return;
        }
        for (String language : names(languages)) {
            if (!(languages.get(language) instanceof ObjectNode texts)) {
                continue;
            }
            ENTITY_SECTIONS.forEach(section -> remapKeys(texts, section, renamed));
            remapAnswers(texts.get("answers"), renamed);
        }
    }

    private static void remapKeys(ObjectNode texts, String section, Map<String, String> renamed) {
        if (!(texts.get(section) instanceof ObjectNode object)) {
            return;
        }
        ObjectNode remapped = texts.objectNode();
        for (String key : names(object)) {
            remapped.set(renamed.getOrDefault(key, key), object.get(key));
        }
        texts.set(section, remapped);
    }

    private static List<String> names(ObjectNode node) {
        List<String> names = new ArrayList<>();
        node.propertyNames().forEach(names::add);
        return names;
    }

    private static void remapAnswers(JsonNode answers, Map<String, String> renamed) {
        if (!(answers instanceof ArrayNode array)) {
            return;
        }
        for (JsonNode entry : array) {
            if (!(entry instanceof ObjectNode object)) {
                continue;
            }
            JsonNode question = object.get("question");
            if (question != null && question.isTextual() && renamed.containsKey(question.asString())) {
                object.put("question", renamed.get(question.asString()));
            }
        }
    }
}
