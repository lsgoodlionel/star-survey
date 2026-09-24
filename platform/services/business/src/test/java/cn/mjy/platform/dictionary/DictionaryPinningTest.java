package cn.mjy.platform.dictionary;

import static cn.mjy.platform.dictionary.DictionaryFixture.GUANGZHOU;
import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import cn.mjy.platform.survey.DraftView;
import cn.mjy.platform.survey.FakePublishGateway;
import cn.mjy.platform.survey.InvalidDefinitionException;
import cn.mjy.platform.survey.SurveyFixture;
import cn.mjy.platform.survey.SurveyFixture.Workspace;
import cn.mjy.platform.survey.SurveyPublishService;
import cn.mjy.platform.survey.SurveyService;
import cn.mjy.platform.survey.SurveyView;
import java.util.ArrayList;
import java.util.List;
import java.util.UUID;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.node.ArrayNode;
import tools.jackson.databind.node.ObjectNode;

/**
 * 字典版本与已发布问卷的绑定（ADR 0019 决定 2）。
 *
 * <p>要钉死的那条不变式：<b>发布的那一刻把版本固化进定义快照</b>。字典之后再怎么更新，
 * 已发布的那一版问卷仍按当初那一版判定路径；想用新字典就重新发布——那本来就是一份新的引擎问卷
 * （ADR 0012 决定 1），与「新版本即新引擎问卷」是同一条规矩。
 *
 * <p>草稿里写什么版本都不算数：客户端不能自己钉版本，否则「已发布问卷引用确定的一版」这句话
 * 就由客户端说了算了。
 */
@SpringBootTest
class DictionaryPinningTest {

    @Autowired
    private SurveyFixture fixture;

    @Autowired
    private DictionaryFixture dictionaryFixture;

    @Autowired
    private SurveyService surveys;

    @Autowired
    private SurveyPublishService publisher;

    @Autowired
    private FakePublishGateway gateway;

    private Workspace ws;
    private String code;

    @BeforeEach
    void aPublishedDictionaryAndAWorkspace() {
        ws = fixture.workspace();
        code = dictionaryFixture.newDictionaryCode();
        dictionaryFixture.publishedDictionary(code, "2024.1");
    }

    /** 一份带「多级下拉」题的草稿：只写字典代码，不写版本。 */
    private SurveyView cascadingSurvey(String dictionaryCode) {
        ObjectNode definition = fixture.definition();
        ObjectNode question = ((ArrayNode) definition.get("groups").get(0).get("questions")).addObject();
        question.put("uuid", UUID.randomUUID().toString());
        question.put("code", "QREGION");
        question.put("type", "T");
        question.put("text", "所在地区");
        question.put("theme", "mjy-cascading-select");
        ObjectNode options = question.putObject("themeOptions");
        options.put("structureVersion", "r1");
        options.put("dictionary", dictionaryCode);
        options.putArray("levels").add("省").add("市").add("区");
        return surveys.create(ws.owner(), ws.project(), definition);
    }

    private JsonNode sentDefinition(UUID survey) {
        return fixture.parse(gateway.callsFor(survey).getLast().definitionJson());
    }

    private JsonNode cascadingOptions(JsonNode definition) {
        for (JsonNode group : definition.get("groups")) {
            for (JsonNode question : group.get("questions")) {
                if ("QREGION".equals(question.get("code").asString())) {
                    return question.get("themeOptions");
                }
            }
        }
        throw new AssertionError("QREGION not in the definition");
    }

    @Test
    void publishingPinsTheCurrentVersionAndShipsTheSnapshot() {
        UUID survey = cascadingSurvey(code).id();

        publisher.publish(ws.owner(), survey);

        JsonNode definition = sentDefinition(survey);
        JsonNode options = cascadingOptions(definition);
        assertThat(options.get("dictionaryVersion").asString()).isEqualTo("2024.1");
        assertThat(options.get("dictionaryDigest").asString()).matches("dg1:[0-9a-f]{16}");

        JsonNode dictionaries = definition.get("dictionaries");
        assertThat(dictionaries).hasSize(1);
        JsonNode shipped = dictionaries.get(0);
        assertThat(shipped.get("code").asString()).isEqualTo(code);
        assertThat(shipped.get("version").asString()).isEqualTo("2024.1");
        assertThat(shipped.get("digest").asString()).isEqualTo(options.get("dictionaryDigest").asString());
        // 节点是紧凑三元组 [代码, 父代码, 标签]：几千个节点要塞进 1 MiB 的定义里。
        assertThat(shipped.get("nodes")).hasSize(9);
        assertThat(shipped.get("nodes").get(0)).hasSize(3);
    }

    @Test
    void aDraftCannotPinTheVersionItself() {
        ObjectNode definition = fixture.definition();
        ObjectNode question = ((ArrayNode) definition.get("groups").get(0).get("questions")).addObject();
        question.put("uuid", UUID.randomUUID().toString());
        question.put("code", "QREGION");
        question.put("type", "T");
        question.put("text", "所在地区");
        question.put("theme", "mjy-cascading-select");
        ObjectNode options = question.putObject("themeOptions");
        options.put("structureVersion", "r1");
        options.put("dictionary", code);
        options.put("dictionaryVersion", "1999.1");
        options.put("dictionaryDigest", "dg1:0000000000000000");
        definition.putArray("dictionaries").addObject().put("code", code);

        SurveyView created = surveys.create(ws.owner(), ws.project(), definition);
        JsonNode saved = surveys.draft(ws.owner(), created.id()).definition();

        assertThat(cascadingOptions(saved).has("dictionaryVersion")).isFalse();
        assertThat(cascadingOptions(saved).has("dictionaryDigest")).isFalse();
        assertThat(saved.has("dictionaries")).isFalse();
    }

    @Test
    void anAlreadyPublishedSurveyKeepsItsVersionWhenTheDictionaryMovesOn() {
        UUID survey = cascadingSurvey(code).id();
        publisher.publish(ws.owner(), survey);
        String pinned = cascadingOptions(sentDefinition(survey)).get("dictionaryVersion").asString();

        List<DictionaryNodeDraft> grown = new ArrayList<>(DictionaryFixture.divisions());
        grown.add(new DictionaryNodeDraft("440104", GUANGZHOU, "越秀区"));
        dictionaryFixture.publishAnotherVersion(code, "2025.1", grown);

        // 已发布的那一版快照里还是旧版本——快照不可变，重读它不会读到新字典。
        assertThat(cascadingOptions(sentDefinition(survey)).get("dictionaryVersion").asString())
                .isEqualTo(pinned).isEqualTo("2024.1");
    }

    @Test
    void republishingAfterADictionaryUpdatePicksUpTheNewVersion() {
        UUID survey = cascadingSurvey(code).id();
        publisher.publish(ws.owner(), survey);

        List<DictionaryNodeDraft> grown = new ArrayList<>(DictionaryFixture.divisions());
        grown.add(new DictionaryNodeDraft("440104", GUANGZHOU, "越秀区"));
        dictionaryFixture.publishAnotherVersion(code, "2025.1", grown);

        DraftView draft = surveys.draft(ws.owner(), survey);
        ObjectNode changed = (ObjectNode) draft.definition().deepCopy();
        changed.put("title", "改了标题好再发一次");
        surveys.saveDraft(ws.owner(), survey, draft.version(), changed);
        publisher.publish(ws.owner(), survey);

        assertThat(cascadingOptions(sentDefinition(survey)).get("dictionaryVersion").asString())
                .isEqualTo("2025.1");
        assertThat(sentDefinition(survey).get("dictionaries").get(0).get("nodes")).hasSize(10);
    }

    @Test
    void aSurveyWithoutACascadingQuestionShipsNoDictionariesAtAll() {
        UUID survey = fixture.newSurvey(ws).id();

        publisher.publish(ws.owner(), survey);

        assertThat(sentDefinition(survey).has("dictionaries")).isFalse();
    }

    @Test
    void referencingADictionaryThatWasNeverPublishedFailsThePublish() {
        String neverPublished = dictionaryFixture.newDictionaryCode();
        dictionaryFixture.draftWithNodes(neverPublished, "2024.1");
        UUID survey = cascadingSurvey(neverPublished).id();

        assertThatThrownBy(() -> publisher.publish(ws.owner(), survey))
                .isInstanceOf(InvalidDefinitionException.class)
                .hasMessageContaining(neverPublished);
    }

    @Test
    void referencingADictionaryThatDoesNotExistFailsThePublish() {
        UUID survey = cascadingSurvey("no-such-dictionary").id();

        assertThatThrownBy(() -> publisher.publish(ws.owner(), survey))
                .isInstanceOf(InvalidDefinitionException.class)
                .hasMessageContaining("no-such-dictionary");
    }
}
