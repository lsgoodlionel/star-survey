package cn.mjy.platform.response;

import static cn.mjy.platform.engine.EngineEventType.COMPLETED;
import static cn.mjy.platform.response.ResponseFixture.GENERATION;
import static cn.mjy.platform.response.ResponseFixture.PLAIN_FIELD;
import static org.assertj.core.api.Assertions.assertThat;

import cn.mjy.platform.access.ResponseFieldPolicy;
import cn.mjy.platform.response.AnswerBatch.ExtensionAnswer;
import cn.mjy.platform.response.ExportFixture.Member;
import cn.mjy.platform.response.ResponseFixture.Published;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;

/**
 * 扩展副表作答进导出（ADR 0015 增补二）：副表是行×列、每份答卷的行数不同，因此单列一张长表，
 * <b>一个单元格一行</b>——列集与列字典无关，不随作答漂移，也不必截断任何一行。
 *
 * <p>一并钉住三件事：校验没过的那一题仍然留一行（不能看成「没填」）、敏感题的单元格与那一列 JSON 信封
 * 同进同退地遮蔽、单元格里的公式文本照样被 {@link ExportCellGuard} 挡住。
 */
@SpringBootTest
class ResponseExportExtensionsTest {

    private static final String TABLE = "QTABLE";
    private static final String SECRET = "QSECRET";

    @Autowired
    private ExportFixture fixture;

    @Autowired
    private FakeResponseAnswerSource answers;

    private Published p;

    @BeforeEach
    void aSurveyWithTwoSideTableQuestions() {
        p = fixture.responses().publishedSurvey(fixture.responses().extensionDefinition());
    }

    private static Map<String, String> row(String... pairs) {
        Map<String, String> cells = new LinkedHashMap<>();
        for (int i = 0; i < pairs.length; i += 2) {
            cells.put(pairs[i], pairs[i + 1]);
        }
        return cells;
    }

    private void completed(long responseId) {
        fixture.responses().response(p.tenant(), p.instance(), p.sid(), GENERATION, responseId, COMPLETED);
        answers.put(p.instance(), p.sid(), responseId, Map.of(PLAIN_FIELD, "A1"));
    }

    private List<List<String>> exportedExtensions() {
        ExportJobView job = fixture.runToEnd(p.owner(), fixture.create(p.owner(), p, "csv"));
        return ExportFixture.unzipCsv(fixture.download(p.owner(), job)).get("extensions.csv");
    }

    @Test
    void everySideTableCellBecomesOneRowKeyedByResponseQuestionRowAndColumn() {
        completed(1);
        completed(2);
        answers.putExtension(p.instance(), p.sid(), 1, TABLE, new ExtensionAnswer("rt1", true,
                List.of(row("item", "甲", "qty", "2"), row("item", "乙", "qty", "3"))));
        answers.putExtension(p.instance(), p.sid(), 2, TABLE, new ExtensionAnswer("rt1", true,
                List.of(row("item", "丙", "qty", "1"))));

        List<List<String>> rows = exportedExtensions();

        assertThat(rows.get(0)).containsExactly("version", "sid", "responseid", "generation", "question",
                "structureversion", "isvalid", "rowindex", "column", "value");
        assertThat(rows).hasSize(1 + 2 * 2 + 2);
        assertThat(rows.get(1)).containsExactly("1", Long.toString(p.sid()), "1", GENERATION, TABLE, "rt1",
                "true", "0", "item", "甲");
        assertThat(rows.get(2)).containsExactly("1", Long.toString(p.sid()), "1", GENERATION, TABLE, "rt1",
                "true", "0", "qty", "2");
        assertThat(rows.get(3).subList(7, 10)).containsExactly("1", "item", "乙");
        assertThat(rows.get(5)).containsExactly("1", Long.toString(p.sid()), "2", GENERATION, TABLE, "rt1",
                "true", "0", "item", "丙");
    }

    /** 闸门没过的那一次作答在副表里是空的；导出必须留一行说明它不可信，否则会被当成「作答者没填」。 */
    @Test
    void aQuestionRejectedByThePluginGateKeepsARowSoItIsNotReadAsUnanswered() {
        completed(1);
        answers.putExtension(p.instance(), p.sid(), 1, TABLE, new ExtensionAnswer("rt1", false, List.of()));

        List<List<String>> rows = exportedExtensions();

        assertThat(rows).hasSize(2);
        assertThat(rows.get(1)).containsExactly("1", Long.toString(p.sid()), "1", GENERATION, TABLE, "rt1",
                "false", "", "", "");
    }

    /** 敏感题的那一列 JSON 信封会被遮蔽；副表里的同一份数据不能因为换了张表就明文出去。 */
    @Test
    void cellsOfASensitiveQuestionAreMaskedJustLikeItsEngineColumn() {
        completed(1);
        answers.putExtension(p.instance(), p.sid(), 1, SECRET,
                new ExtensionAnswer("rt2", true, List.of(row("name", "张三"))));
        Member manager = fixture.member(p, "pm", "project_manager");

        ExportJobView job = fixture.runToEnd(manager.ctx(), fixture.create(manager.ctx(), p, "csv"));
        List<List<String>> rows = ExportFixture.unzipCsv(fixture.download(manager.ctx(), job)).get("extensions.csv");

        assertThat(rows).hasSize(2);
        assertThat(rows.get(1)).containsExactly("1", Long.toString(p.sid()), "1", GENERATION, SECRET, "rt2",
                "true", "0", "name", ResponseFieldPolicy.MASK);
    }

    /** 防公式注入对新列一样生效：表格软件打开时按文本显示，不执行。 */
    @Test
    void formulaTextInACellIsGuardedLikeEveryOtherCell() {
        completed(1);
        answers.putExtension(p.instance(), p.sid(), 1, TABLE, new ExtensionAnswer("rt1", true,
                List.of(row("item", "=cmd|' /C calc'!A0", "qty", "-12.5"))));

        List<List<String>> rows = exportedExtensions();

        assertThat(rows.get(1)).endsWith("item", "'=cmd|' /C calc'!A0");
        assertThat(rows.get(2)).endsWith("qty", "-12.5");
    }

    /** 副表题在作业计划里点名，代次取计划冻结的那一个——两者一起给才读得到副表。 */
    @Test
    void theGatewayIsAskedForTheFrozenSideTableQuestionsAndGeneration() {
        completed(1);

        fixture.runToEnd(p.owner(), fixture.create(p.owner(), p, "csv"));

        assertThat(answers.callsFor(p.instance())).isNotEmpty()
                .allSatisfy(call -> {
                    assertThat(call.extensionQuestions()).containsExactly(TABLE, SECRET);
                    assertThat(call.generation()).isEqualTo(GENERATION);
                });
    }

    /** 没有任何副表作答时这张表仍然在，只有表头——附表在不在不该随数据变。 */
    @Test
    void theSheetIsAlwaysPresentEvenWithoutAnySideTableAnswers() {
        completed(1);

        assertThat(exportedExtensions()).hasSize(1);
    }

    /** 结构版本 "0"（列字典无从考证）照样落得下：列代码原样呈现，不猜字典。 */
    @Test
    void cellsWrittenBeforeTheContractKeepTheirColumnCodesVerbatim() {
        completed(1);
        answers.putExtension(p.instance(), p.sid(), 1, TABLE,
                new ExtensionAnswer("0", true, List.of(row("legacy_col", "x"))));

        List<List<String>> rows = exportedExtensions();

        assertThat(rows.get(1)).containsSequence("0", "true", "0", "legacy_col", "x");
    }
}
