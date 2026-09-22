package cn.mjy.platform.response;

import static org.assertj.core.api.Assertions.assertThat;

import java.util.List;
import java.util.UUID;
import org.junit.jupiter.api.Test;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.json.JsonMapper;
import cn.mjy.platform.survey.PublishedVersionView.QuestionFieldView;

/** 字段字典由定义快照与绑定映射推出：标签、选项、敏感标记，每一列都能追溯到题目 UUID。 */
class FieldDictionaryTest {

    private static final UUID SINGLE = UUID.fromString("33333333-0001-4111-8111-000000000001");
    private static final UUID PHONE = UUID.fromString("33333333-0002-4111-8111-000000000002");
    private static final UUID MULTI = UUID.fromString("33333333-0004-4111-8111-000000000004");
    private static final UUID DUAL = UUID.fromString("33333333-0005-4111-8111-000000000005");

    private final JsonMapper json = JsonMapper.builder().build();

    private final JsonNode definition = json.readTree("""
            {"groups":[{"questions":[
              {"uuid":"33333333-0001-4111-8111-000000000001","code":"QSINGLE","type":"L","text":"选一个",
               "other":true,"answers":[{"code":"A1","text":"第一"},{"code":"A2","text":"第二"}]},
              {"uuid":"33333333-0002-4111-8111-000000000002","code":"QPHONE","type":"S","text":"手机号",
               "sensitive":true},
              {"uuid":"33333333-0004-4111-8111-000000000004","code":"QMULTI","type":"P","text":"可多选",
               "subquestions":[{"code":"SQ001","text":"速度"}]},
              {"uuid":"33333333-0005-4111-8111-000000000005","code":"QDUAL","type":"1","text":"双尺度",
               "subquestions":[{"code":"SQ001","text":"服务"}],
               "answers":[{"code":"L1","text":"低","scale":0},{"code":"H1","text":"高","scale":1}]}
            ]}]}
            """);

    private final List<QuestionFieldView> binding = List.of(
            new QuestionFieldView(SINGLE, "QSINGLE", "L", "9X1X1", "", 0),
            new QuestionFieldView(SINGLE, "QSINGLE", "L", "9X1X1other", "other", 0),
            new QuestionFieldView(PHONE, "QPHONE", "S", "9X1X2", "", 0),
            new QuestionFieldView(MULTI, "QMULTI", "P", "9X1X4SQ001", "SQ001", 0),
            new QuestionFieldView(MULTI, "QMULTI", "P", "9X1X4SQ001comment", "SQ001comment", 0),
            new QuestionFieldView(DUAL, "QDUAL", "1", "9X1X5SQ001#0", "SQ001", 0),
            new QuestionFieldView(DUAL, "QDUAL", "1", "9X1X5SQ001#1", "SQ001", 1));

    private List<FieldDictionary.FieldEntry> fields() {
        return FieldDictionaryBuilder.fields(definition, binding);
    }

    @Test
    void everyBoundColumnBecomesOneEntryInBindingOrder() {
        assertThat(fields()).extracting(FieldDictionary.FieldEntry::fieldname).containsExactly(
                "9X1X1", "9X1X1other", "9X1X2", "9X1X4SQ001", "9X1X4SQ001comment", "9X1X5SQ001#0", "9X1X5SQ001#1");
        assertThat(fields().get(0).questionUuid()).isEqualTo(SINGLE);
        assertThat(fields().get(0).code()).isEqualTo("QSINGLE");
        assertThat(fields().get(0).type()).isEqualTo("L");
    }

    @Test
    void labelsComeFromTheQuestionAndSubquestionTexts() {
        assertThat(fields()).extracting(FieldDictionary.FieldEntry::label).containsExactly(
                "选一个", "选一个 [其他]", "手机号", "可多选 [速度]", "可多选 [速度]（评论）", "双尺度 [服务]", "双尺度 [服务]");
    }

    @Test
    void optionLabelsFollowTheColumnsScale() {
        List<FieldDictionary.FieldEntry> fields = fields();
        assertThat(fields.get(0).options()).extracting(FieldDictionary.OptionLabel::code).containsExactly("A1", "A2");
        assertThat(fields.get(1).options()).isEmpty();
        assertThat(fields.get(5).options()).extracting(FieldDictionary.OptionLabel::text).containsExactly("低");
        assertThat(fields.get(6).options()).extracting(FieldDictionary.OptionLabel::text).containsExactly("高");
    }

    @Test
    void onlyColumnsOfQuestionsMarkedSensitiveAreSensitive() {
        assertThat(fields()).filteredOn(FieldDictionary.FieldEntry::sensitive)
                .extracting(FieldDictionary.FieldEntry::fieldname).containsExactly("9X1X2");
    }

    @Test
    void aColumnWhoseQuestionIsMissingFromTheSnapshotFallsBackToTheCode() {
        List<FieldDictionary.FieldEntry> fields = FieldDictionaryBuilder.fields(json.readTree("{\"groups\":[]}"),
                List.of(new QuestionFieldView(PHONE, "QPHONE", "S", "9X1X2", "", 0)));

        assertThat(fields).singleElement().satisfies(f -> {
            assertThat(f.label()).isEqualTo("QPHONE");
            assertThat(f.sensitive()).isFalse();
        });
    }
}
