package cn.mjy.platform.response;

import static org.assertj.core.api.Assertions.assertThat;

import cn.mjy.platform.survey.PublishedVersionView.QuestionFieldView;
import java.util.List;
import java.util.UUID;
import org.junit.jupiter.api.Test;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.json.JsonMapper;

/**
 * WP-02 题型（网关 {@code pubgw/qtypes.py}，映射表 platform/docs/p2/question-type-map.md）的字段字典：
 * 引擎内置刻度的选项、矩阵"行_列"、排序的 JSON 列与名次列、评论列与文件计数列。
 */
class FieldDictionaryQuestionTypesTest {

    private static final UUID STAR = UUID.fromString("44444444-0001-4111-8111-000000000001");
    private static final UUID YESNO = UUID.fromString("44444444-0002-4111-8111-000000000002");
    private static final UUID ARRAY5 = UUID.fromString("44444444-0003-4111-8111-000000000003");
    private static final UUID MATRIX = UUID.fromString("44444444-0004-4111-8111-000000000004");
    private static final UUID RANK = UUID.fromString("44444444-0005-4111-8111-000000000005");
    private static final UUID COMMENT = UUID.fromString("44444444-0006-4111-8111-000000000006");
    private static final UUID UPLOAD = UUID.fromString("44444444-0007-4111-8111-000000000007");
    private static final UUID MULTI = UUID.fromString("44444444-0008-4111-8111-000000000008");
    private static final UUID ALLOC = UUID.fromString("44444444-0009-4111-8111-000000000009");

    private final JsonMapper json = JsonMapper.builder().build();

    private final JsonNode definition = json.readTree("""
            {"groups":[{"questions":[
              {"uuid":"44444444-0001-4111-8111-000000000001","code":"QSTAR","type":"5","text":"星级"},
              {"uuid":"44444444-0002-4111-8111-000000000002","code":"QYES","type":"Y","text":"是否"},
              {"uuid":"44444444-0003-4111-8111-000000000003","code":"QA5","type":"A","text":"五分矩阵",
               "subquestions":[{"code":"R1","text":"界面"}]},
              {"uuid":"44444444-0004-4111-8111-000000000004","code":"QMNUM","type":":","text":"数值矩阵",
               "subquestions":[{"code":"R1","text":"一月"},{"code":"C1","text":"收入","scale":1}]},
              {"uuid":"44444444-0005-4111-8111-000000000005","code":"QRANK","type":"R","text":"排序",
               "subquestions":[{"code":"I1","text":"价格"},{"code":"I2","text":"品牌"}]},
              {"uuid":"44444444-0006-4111-8111-000000000006","code":"QCOM","type":"O","text":"单选带评论",
               "answers":[{"code":"A1","text":"满意"}]},
              {"uuid":"44444444-0007-4111-8111-000000000007","code":"QFILE","type":"|","text":"附件"},
              {"uuid":"44444444-0008-4111-8111-000000000008","code":"QMULTI","type":"M","text":"多选",
               "subquestions":[{"code":"S1","text":"价格"}]},
              {"uuid":"44444444-0009-4111-8111-000000000009","code":"QALLOC","type":"K","text":"分配",
               "subquestions":[{"code":"P1","text":"线上"}]}
            ]}]}
            """);

    private final List<QuestionFieldView> binding = List.of(
            new QuestionFieldView(STAR, "QSTAR", "5", "Q1", "", 0),
            new QuestionFieldView(YESNO, "QYES", "Y", "Q2", "", 0),
            new QuestionFieldView(ARRAY5, "QA5", "A", "Q3_S31", "R1", 0),
            new QuestionFieldView(MATRIX, "QMNUM", ":", "Q4_S41_S42", "R1_C1", 0),
            new QuestionFieldView(RANK, "QRANK", "R", "Q5", "", 0),
            new QuestionFieldView(RANK, "QRANK", "R", "Q5_S51", "1", 0),
            new QuestionFieldView(RANK, "QRANK", "R", "Q5_S52", "2", 0),
            new QuestionFieldView(COMMENT, "QCOM", "O", "Q6", "", 0),
            new QuestionFieldView(COMMENT, "QCOM", "O", "Q6_Ccomment", "comment", 0),
            new QuestionFieldView(UPLOAD, "QFILE", "|", "Q7", "", 0),
            new QuestionFieldView(UPLOAD, "QFILE", "|", "Q7_Cfilecount", "filecount", 0),
            new QuestionFieldView(MULTI, "QMULTI", "M", "Q8_S81", "S1", 0),
            new QuestionFieldView(ALLOC, "QALLOC", "K", "Q9_S91", "P1", 0));

    private FieldDictionary.FieldEntry field(String fieldname) {
        return FieldDictionaryBuilder.fields(definition, binding).stream()
                .filter(entry -> entry.fieldname().equals(fieldname)).findFirst().orElseThrow();
    }

    private static List<String> codes(FieldDictionary.FieldEntry entry) {
        return entry.options().stream().map(FieldDictionary.OptionLabel::code).toList();
    }

    @Test
    void builtInScalesListTheEngineCodes() {
        assertThat(codes(field("Q1"))).containsExactly("1", "2", "3", "4", "5");
        assertThat(codes(field("Q2"))).containsExactly("Y", "N");
        assertThat(field("Q2").options()).extracting(FieldDictionary.OptionLabel::text).containsExactly("是", "否");
        assertThat(codes(field("Q3_S31"))).containsExactly("1", "2", "3", "4", "5");
        assertThat(field("Q3_S31").label()).isEqualTo("五分矩阵 [界面]");
    }

    @Test
    void matrixCellsAreLabelledByRowAndColumn() {
        assertThat(field("Q4_S41_S42").label()).isEqualTo("数值矩阵 [一月 / 收入]");
        assertThat(field("Q4_S41_S42").options()).isEmpty();
    }

    @Test
    void rankingColumnsListTheItemsAndNameTheRank() {
        assertThat(field("Q5").label()).isEqualTo("排序");
        assertThat(codes(field("Q5"))).containsExactly("I1", "I2");
        assertThat(field("Q5_S51").label()).isEqualTo("排序 [第1位]");
        assertThat(field("Q5_S52").options()).extracting(FieldDictionary.OptionLabel::text).containsExactly("价格", "品牌");
    }

    @Test
    void commentAndFileCountColumnsAreFreeValues() {
        assertThat(codes(field("Q6"))).containsExactly("A1");
        assertThat(field("Q6_Ccomment").label()).isEqualTo("单选带评论（评论）");
        assertThat(field("Q6_Ccomment").options()).isEmpty();
        assertThat(field("Q7_Cfilecount").label()).isEqualTo("附件 [文件数]");
        assertThat(field("Q7_Cfilecount").options()).isEmpty();
    }

    @Test
    void multipleChoiceColumnsAreCheckedOrNot() {
        assertThat(field("Q8_S81").options()).extracting(FieldDictionary.OptionLabel::code).containsExactly("Y");
    }

    @Test
    void numericSubquestionColumnsHaveNoOptions() {
        assertThat(field("Q9_S91").label()).isEqualTo("分配 [线上]");
        assertThat(field("Q9_S91").options()).isEmpty();
    }
}
