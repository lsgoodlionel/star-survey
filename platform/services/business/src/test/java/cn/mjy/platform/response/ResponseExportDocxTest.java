package cn.mjy.platform.response;

import static cn.mjy.platform.engine.EngineEventType.COMPLETED;
import static cn.mjy.platform.engine.EngineEventType.DELETED;
import static cn.mjy.platform.response.ResponseFixture.GENERATION;
import static cn.mjy.platform.response.ResponseFixture.PLAIN_FIELD;
import static cn.mjy.platform.response.ResponseFixture.SENSITIVE_FIELD;
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
 * 整条作业链路产出的 Word 文档（R06-05 / R06-06）：建作业 → 后台跑完 → 下载 → 读回。
 * 逐份答卷成文，副表作答还原成表格，敏感列照旧遮蔽，删除的答卷仍然占一份（只是没有作答）。
 */
@SpringBootTest
class ResponseExportDocxTest {

    @Autowired
    private ExportFixture fixture;

    @Autowired
    private FakeResponseAnswerSource answers;

    private Published p;

    @BeforeEach
    void twoResponsesOneOfThemDeleted() {
        p = fixture.responses().publishedSurvey(fixture.responses().extensionDefinition());
        fixture.responses().response(p.tenant(), p.instance(), p.sid(), GENERATION, 1, COMPLETED);
        answers.put(p.instance(), p.sid(), 1, Map.of(PLAIN_FIELD, "A1", SENSITIVE_FIELD, "13812345678"));
        answers.putExtension(p.instance(), p.sid(), 1, "QTABLE", new ExtensionAnswer("rt1", true,
                List.of(row("item", "帐篷", "qty", "1"), row("item", "睡袋", "qty", "2"))));
        fixture.responses().response(p.tenant(), p.instance(), p.sid(), GENERATION, 2, DELETED);
    }

    private static Map<String, String> row(String... pairs) {
        Map<String, String> cells = new LinkedHashMap<>();
        for (int i = 0; i < pairs.length; i += 2) {
            cells.put(pairs[i], pairs[i + 1]);
        }
        return cells;
    }

    private List<String> texts(ExportFixture.Member member) {
        ExportJobView job = fixture.runToEnd(member.ctx(), fixture.create(member.ctx(), p, "docx"));
        assertThat(job.status()).isEqualTo("completed");
        return ExportFixture.docxTexts(fixture.download(member.ctx(), job));
    }

    @Test
    void everyResponseBecomesItsOwnSectionWithQuestionsAndAnswers() {
        ExportJobView job = fixture.runToEnd(p.owner(), fixture.create(p.owner(), p, "docx"));
        byte[] docx = fixture.download(p.owner(), job);
        List<String> texts = ExportFixture.docxTexts(docx);

        assertThat(job.status()).isEqualTo("completed");
        assertThat(texts).contains("答卷 1", "答卷 2", "Pick exactly one", "A1", "作答状态", "deleted");
        assertThat(docx).startsWith(0x50, 0x4B);
    }

    /** 副表作答在文档里是一张真表：一列一列、一行一行，不是导出长表里的那一堆单元格。 */
    @Test
    void sideTableAnswersAreRenderedAsARealTable() {
        List<String> texts = texts(fixture.grant(p, p.owner(), "raw_data_viewer"));

        assertThat(texts).contains("随行物品 [结构化作答]（结构版本 rt1）", "item", "qty", "帐篷", "睡袋", "2");
    }

    @Test
    void sensitiveAnswersAreMaskedInTheDocumentToo() {
        Member manager = fixture.member(p, "pm", "project_manager");

        List<String> texts = texts(manager);

        assertThat(texts).contains(ResponseFieldPolicy.MASK).doesNotContain("13812345678");
    }

    /** 崩溃恢复与表格格式同一条路：重跑得到逐字节相同的文档。 */
    @Test
    void theSameJobRunTwiceProducesTheSameDocument() {
        ExportJobView first = fixture.runToEnd(p.owner(), fixture.create(p.owner(), p, "docx"));
        ExportJobView second = fixture.runToEnd(p.owner(), fixture.create(p.owner(), p, "docx"));

        assertThat(fixture.download(p.owner(), first)).isEqualTo(fixture.download(p.owner(), second));
        assertThat(first.sha256()).isEqualTo(second.sha256());
    }
}
