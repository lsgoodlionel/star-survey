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
 * 整条作业链路产出的 PDF（R06-05，ADR 0015 增补六）：建作业 → 后台跑完 → 下载 → 读回。
 * 与 Word 那一半共用同一套 {@link ExportRecord} 视图，所以这里核对的是<b>格式特有</b>的那几件事：
 * 中文真的能提取、字体真的嵌进来了、敏感列照旧遮蔽、重跑逐字节相同。
 */
@SpringBootTest
class ResponseExportPdfTest {

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

    private byte[] download(Member member) {
        ExportJobView job = fixture.runToEnd(member.ctx(), fixture.create(member.ctx(), p, "pdf"));
        assertThat(job.status()).isEqualTo("completed");
        return fixture.download(member.ctx(), job);
    }

    /** 整份文档的文本，经 {@code ToUnicode} 还原——与真阅读器取文本的路径相同。 */
    private static String allText(byte[] pdf) {
        return PdfReader.of(pdf).pages().stream().map(PdfReader.Page::text).reduce("", String::concat);
    }

    @Test
    void everyResponseBecomesItsOwnPageWithQuestionsAndAnswers() {
        ExportJobView job = fixture.runToEnd(p.owner(), fixture.create(p.owner(), p, "pdf"));
        byte[] pdf = fixture.download(p.owner(), job);

        assertThat(job.status()).isEqualTo("completed");
        assertThat(pdf).startsWith('%', 'P', 'D', 'F');
        assertThat(PdfReader.of(pdf).pages()).hasSize(2);
        assertThat(allText(pdf)).contains("答卷 1", "答卷 2", "Pick exactly one", "A1", "作答状态", "deleted");
    }

    /** 中文标签必须是**提取得出来**的文本，不是画在页面上的图形。 */
    @Test
    void theChineseLabelsAreExtractableAndTheFontIsEmbedded() {
        byte[] pdf = fixture.download(p.owner(),
                fixture.runToEnd(p.owner(), fixture.create(p.owner(), p, "pdf")));

        assertThat(allText(pdf)).contains("答卷信息", "版本", "答卷号", "作答状态", "作答");
        assertThat(PdfReader.of(pdf).embeddedFont().tables().get("glyf")).isNotEmpty();
    }

    @Test
    void sideTableAnswersAreRenderedAsARealTable() {
        byte[] pdf = download(fixture.grant(p, p.owner(), "raw_data_viewer"));

        assertThat(allText(pdf)).contains("随行物品 [结构化作答]（结构版本 rt1）", "item", "qty", "帐篷", "睡袋");
    }

    @Test
    void sensitiveAnswersAreMaskedInThePdfToo() {
        Member manager = fixture.member(p, "pm", "project_manager");

        String text = allText(download(manager));

        assertThat(text).contains(ResponseFieldPolicy.MASK).doesNotContain("13812345678");
    }

    /** 崩溃恢复与表格格式同一条路：重跑得到逐字节相同的文档。 */
    @Test
    void theSameJobRunTwiceProducesTheSamePdf() {
        ExportJobView first = fixture.runToEnd(p.owner(), fixture.create(p.owner(), p, "pdf"));
        ExportJobView second = fixture.runToEnd(p.owner(), fixture.create(p.owner(), p, "pdf"));

        assertThat(fixture.download(p.owner(), first)).isEqualTo(fixture.download(p.owner(), second));
        assertThat(first.sha256()).isEqualTo(second.sha256());
    }
}
