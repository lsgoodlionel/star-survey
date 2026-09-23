package cn.mjy.platform.response;

import static org.assertj.core.api.Assertions.assertThat;

import cn.mjy.platform.survey.PublishedVersionView.QuestionFieldView;
import java.util.List;
import java.util.UUID;
import org.junit.jupiter.api.Test;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.json.JsonMapper;

/**
 * WP-02 切片 02.3 的平台题型主题（网关 {@code pubgw/questions/themes.py}）在字段字典里的形状。
 *
 * <ul>
 *   <li>副表题型（自增表格、热力图）：引擎那一列是整块 JSON 信封，真正的结构在副表里
 *       （platform/contracts/question-extension-tables-v1.md），所以标注成"结构化作答"且没有可枚举取值；</li>
 *   <li>选项内嵌填空：填空落在多选带评论的评论列上，标签用主题配置的提示语；</li>
 *   <li>纯展示主题（折叠栏目、扫码录入、选项分类、矩阵单题作答）：列的含义与不带主题时完全一样。</li>
 * </ul>
 */
class FieldDictionaryQuestionThemesTest {

    private static final UUID TABLE = UUID.fromString("45454545-0001-4111-8111-000000000001");
    private static final UUID HEAT = UUID.fromString("45454545-0002-4111-8111-000000000002");
    private static final UUID BLANK = UUID.fromString("45454545-0003-4111-8111-000000000003");
    private static final UUID GROUPED = UUID.fromString("45454545-0004-4111-8111-000000000004");
    private static final UUID SCAN = UUID.fromString("45454545-0005-4111-8111-000000000005");
    private static final UUID SECTION = UUID.fromString("45454545-0006-4111-8111-000000000006");

    private final JsonMapper json = JsonMapper.builder().build();

    private final JsonNode definition = json.readTree("""
            {"groups":[{"questions":[
              {"uuid":"45454545-0001-4111-8111-000000000001","code":"QTABLE","type":"T","text":"随行物品",
               "theme":"mjy-repeating-table",
               "themeOptions":{"structureVersion":"rt1","columns":[{"code":"item","type":"text"}]}},
              {"uuid":"45454545-0002-4111-8111-000000000002","code":"QHEAT","type":"T","text":"关注区域",
               "theme":"mjy-heatmap","themeOptions":{"structureVersion":"hm1","image":"x.png"}},
              {"uuid":"45454545-0003-4111-8111-000000000003","code":"QBLANK","type":"P","text":"选择并补充",
               "theme":"mjy-inline-blank","themeOptions":{"blankLabel":"型号","blankMaxLength":30},
               "subquestions":[{"code":"S1","text":"手机"}]},
              {"uuid":"45454545-0004-4111-8111-000000000004","code":"QGRP","type":"L","text":"喜欢的食物",
               "theme":"mjy-grouped-options",
               "answers":[{"code":"A1","text":"苹果"},{"code":"A2","text":"白菜"}]},
              {"uuid":"45454545-0005-4111-8111-000000000005","code":"QSCAN","type":"S","text":"扫码",
               "theme":"mjy-scan-input","themeOptions":{"scanFormat":"qr"}},
              {"uuid":"45454545-0006-4111-8111-000000000006","code":"QSEC","type":"X","text":"家庭情况",
               "theme":"mjy-collapsible","themeOptions":{"summary":"家庭情况"}}
            ]}]}
            """);

    private final List<QuestionFieldView> binding = List.of(
            new QuestionFieldView(TABLE, "QTABLE", "T", "Q1", "", 0),
            new QuestionFieldView(HEAT, "QHEAT", "T", "Q2", "", 0),
            new QuestionFieldView(BLANK, "QBLANK", "P", "Q3_S31", "S1", 0),
            new QuestionFieldView(BLANK, "QBLANK", "P", "Q3_S31_Ccomment", "S1comment", 0),
            new QuestionFieldView(GROUPED, "QGRP", "L", "Q4", "", 0),
            new QuestionFieldView(SCAN, "QSCAN", "S", "Q5", "", 0),
            new QuestionFieldView(SECTION, "QSEC", "X", "Q6", "", 0));

    private FieldDictionary.FieldEntry field(String fieldname) {
        return FieldDictionaryBuilder.fields(definition, binding).stream()
                .filter(entry -> entry.fieldname().equals(fieldname)).findFirst().orElseThrow();
    }

    private static List<String> codes(FieldDictionary.FieldEntry entry) {
        return entry.options().stream().map(FieldDictionary.OptionLabel::code).toList();
    }

    @Test
    void sideTableQuestionsAreMarkedAsStructuredAndHaveNoEnumerableValues() {
        assertThat(field("Q1").label()).isEqualTo("随行物品 [结构化作答]");
        assertThat(field("Q1").options()).isEmpty();
        assertThat(field("Q2").label()).isEqualTo("关注区域 [结构化作答]");
        assertThat(field("Q2").options()).isEmpty();
    }

    @Test
    void inlineBlankCommentColumnsUseTheConfiguredBlankLabel() {
        assertThat(codes(field("Q3_S31"))).containsExactly("Y");
        assertThat(field("Q3_S31_Ccomment").label()).isEqualTo("选择并补充 [手机]（型号）");
        assertThat(field("Q3_S31_Ccomment").options()).isEmpty();
    }

    @Test
    void presentationOnlyThemesLeaveTheColumnMeaningUntouched() {
        assertThat(field("Q4").label()).isEqualTo("喜欢的食物");
        assertThat(codes(field("Q4"))).containsExactly("A1", "A2");
        assertThat(field("Q5").label()).isEqualTo("扫码");
        assertThat(field("Q5").options()).isEmpty();
        assertThat(field("Q6").label()).isEqualTo("家庭情况");
        assertThat(field("Q6").options()).isEmpty();
    }
}
