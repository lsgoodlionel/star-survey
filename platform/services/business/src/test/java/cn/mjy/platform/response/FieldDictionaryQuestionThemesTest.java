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
    private static final UUID GROUPED_MULTI = UUID.fromString("45454545-0007-4111-8111-000000000007");
    private static final UUID LOOP = UUID.fromString("45454545-0008-4111-8111-000000000008");
    private static final UUID PK = UUID.fromString("45454545-0009-4111-8111-000000000009");
    private static final UUID SHELF = UUID.fromString("45454545-0010-4111-8111-000000000010");
    private static final UUID MARK = UUID.fromString("45454545-0011-4111-8111-000000000011");
    private static final UUID PSYCH = UUID.fromString("45454545-0012-4111-8111-000000000012");

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
               "theme":"mjy-collapsible","themeOptions":{"summary":"家庭情况"}},
              {"uuid":"45454545-0007-4111-8111-000000000007","code":"QGRPM","type":"M","text":"买过哪些",
               "theme":"mjy-grouped-options","other":true,
               "subquestions":[{"code":"M1","text":"苹果"},{"code":"M2","text":"白菜"}]},
              {"uuid":"45454545-0008-4111-8111-000000000008","code":"QLOOP","type":"T","text":"逐个品牌评价",
               "theme":"mjy-loop-rating",
               "themeOptions":{"structureVersion":"lr1","objects":[{"code":"B1","label":"甲品牌"}],
                               "dimensions":[{"code":"price","label":"价格"}],
                               "scale":[{"code":"1","label":"差"}]}},
              {"uuid":"45454545-0009-4111-8111-000000000009","code":"QPK","type":"T","text":"哪个包装更好",
               "theme":"mjy-image-pk",
               "themeOptions":{"structureVersion":"pk1",
                               "items":[{"code":"A","label":"甲","image":"a.png"},
                                        {"code":"B","label":"乙","image":"b.png"}],
                               "pairs":[{"code":"P1","left":"A","right":"B"}]}},
              {"uuid":"45454545-0010-4111-8111-000000000010","code":"QSHELF","type":"T","text":"从货架上取货",
               "theme":"mjy-shelf",
               "themeOptions":{"structureVersion":"sh1","image":"shelf.png",
                               "products":[{"code":"S1","label":"牛奶","x":0.1,"y":0.2,"w":0.2,"h":0.3}]}},
              {"uuid":"45454545-0011-4111-8111-000000000011","code":"QMARK","type":"T","text":"在原文上标记",
               "theme":"mjy-text-highlight",
               "themeOptions":{"structureVersion":"th1","text":"苹果很甜。",
                               "segments":[{"start":0,"length":4}],
                               "tags":[{"code":"like","label":"喜欢"}]}},
              {"uuid":"45454545-0012-4111-8111-000000000012","code":"QPSY","type":"T","text":"颜色判断实验",
               "theme":"mjy-psych-trial",
               "themeOptions":{"structureVersion":"ps1",
                               "trials":[{"code":"T1","label":"第一试次","stimulus":"红","correct":"left"}],
                               "keys":[{"code":"left","label":"左键 F"}]}}
            ]}]}
            """);

    private final List<QuestionFieldView> binding = List.of(
            new QuestionFieldView(TABLE, "QTABLE", "T", "Q1", "", 0),
            new QuestionFieldView(HEAT, "QHEAT", "T", "Q2", "", 0),
            new QuestionFieldView(BLANK, "QBLANK", "P", "Q3_S31", "S1", 0),
            new QuestionFieldView(BLANK, "QBLANK", "P", "Q3_S31_Ccomment", "S1comment", 0),
            new QuestionFieldView(GROUPED, "QGRP", "L", "Q4", "", 0),
            new QuestionFieldView(SCAN, "QSCAN", "S", "Q5", "", 0),
            new QuestionFieldView(SECTION, "QSEC", "X", "Q6", "", 0),
            new QuestionFieldView(GROUPED_MULTI, "QGRPM", "M", "Q7_S71", "M1", 0),
            new QuestionFieldView(GROUPED_MULTI, "QGRPM", "M", "Q7_S72", "M2", 0),
            new QuestionFieldView(GROUPED_MULTI, "QGRPM", "M", "Q7_other", "other", 0),
            new QuestionFieldView(LOOP, "QLOOP", "T", "Q8", "", 0),
            new QuestionFieldView(PK, "QPK", "T", "Q9", "", 0),
            new QuestionFieldView(SHELF, "QSHELF", "T", "Q10", "", 0),
            new QuestionFieldView(MARK, "QMARK", "T", "Q11", "", 0),
            new QuestionFieldView(PSYCH, "QPSY", "T", "Q12", "", 0));

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
        // R02-11 循环评价：对象与维度都在副表里，引擎那一列同样是整块信封。
        assertThat(field("Q8").label()).isEqualTo("逐个品牌评价 [结构化作答]");
        assertThat(field("Q8").options()).isEmpty();
        // R02-17 图片 PK：每一对的选择都在副表里。
        assertThat(field("Q9").label()).isEqualTo("哪个包装更好 [结构化作答]");
        assertThat(field("Q9").options()).isEmpty();
        // R02-18 货架题：取了什么、取了几件都在副表里。
        assertThat(field("Q10").label()).isEqualTo("从货架上取货 [结构化作答]");
        assertThat(field("Q10").options()).isEmpty();
        // R02-22 文字点睛：标了哪几段、各标成什么都在副表里。
        assertThat(field("Q11").label()).isEqualTo("在原文上标记 [结构化作答]");
        assertThat(field("Q11").options()).isEmpty();
        // R02-46 心理实验：试次、按键与反应时都在副表里；正确率由平台按定义里的
        // trials[].correct 推导，引擎那一列不含对错。
        assertThat(field("Q12").label()).isEqualTo("颜色判断实验 [结构化作答]");
        assertThat(field("Q12").options()).isEmpty();
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

    /**
     * R02-04 的多选分支（第四波遗留）：分组只换展示，每个子题仍然是自己的一列、
     * 选中仍然是 {@code Y}，「其他」列仍然是自由文本。字段字典不因为带了分组主题而改形状。
     */
    @Test
    void groupedMultipleChoiceKeepsOneColumnPerSubquestion() {
        assertThat(field("Q7_S71").label()).isEqualTo("买过哪些 [苹果]");
        assertThat(codes(field("Q7_S71"))).containsExactly("Y");
        assertThat(field("Q7_S72").label()).isEqualTo("买过哪些 [白菜]");
        assertThat(codes(field("Q7_S72"))).containsExactly("Y");
        assertThat(field("Q7_other").options()).isEmpty();
    }
}
