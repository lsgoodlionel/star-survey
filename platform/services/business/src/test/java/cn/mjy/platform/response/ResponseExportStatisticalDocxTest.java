package cn.mjy.platform.response;

import static cn.mjy.platform.engine.EngineEventType.COMPLETED;
import static cn.mjy.platform.response.ResponseFixture.GENERATION;
import static cn.mjy.platform.response.ResponseFixture.PLAIN_FIELD;
import static org.assertj.core.api.Assertions.assertThat;

import cn.mjy.platform.response.ResponseFixture.Published;
import java.util.List;
import java.util.Map;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;

/** R06-04：统计报告必须通过真实导出作业冻结、生成并下载，而不是前端拼装假文件。 */
@SpringBootTest
class ResponseExportStatisticalDocxTest {

    @Autowired private ExportFixture fixture;
    @Autowired private FakeResponseAnswerSource answers;
    private Published published;

    @BeforeEach
    void completedResponses() {
        published = fixture.responses().publishedSurvey();
        for (int responseId = 1; responseId <= 3; responseId++) {
            fixture.responses().response(published.tenant(), published.instance(), published.sid(), GENERATION,
                    responseId, COMPLETED);
            answers.put(published.instance(), published.sid(), responseId,
                    Map.of(PLAIN_FIELD, responseId < 3 ? "满意" : "一般"));
        }
    }

    @Test
    void completedJobDownloadsAReadableStatisticalWordReport() {
        ExportJobView job = fixture.runToEnd(published.owner(),
                fixture.create(published.owner(), published, "report-docx"));
        byte[] docx = fixture.download(published.owner(), job);
        List<String> texts = ExportFixture.docxTexts(docx);

        assertThat(job.status()).isEqualTo("completed");
        assertThat(job.format()).isEqualTo("report-docx");
        assertThat(docx).startsWith(0x50, 0x4B);
        assertThat(texts).contains("答卷统计报告", "答卷总数", "3", "满意", "2", "66.67%", "一般", "1");
    }
}
