package cn.mjy.platform.response;

import static cn.mjy.platform.engine.EngineEventType.COMPLETED;
import static cn.mjy.platform.response.ResponseFixture.GENERATION;
import static cn.mjy.platform.response.ResponseFixture.PLAIN_FIELD;
import static org.assertj.core.api.Assertions.assertThat;

import cn.mjy.platform.response.ResponseFixture.Published;
import java.text.Normalizer;
import java.util.Map;
import org.apache.pdfbox.Loader;
import org.apache.pdfbox.text.PDFTextStripper;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;

/** 整条作业链路的 PDF 产物沿用既有快照、权限、后台处理和下载再授权。 */
@SpringBootTest
class ResponseExportPdfTest {

    @Autowired private ExportFixture fixture;
    @Autowired private FakeResponseAnswerSource answers;
    private Published published;

    @BeforeEach
    void completedResponse() {
        published = fixture.responses().publishedSurvey();
        fixture.responses().response(published.tenant(), published.instance(), published.sid(), GENERATION, 1,
                COMPLETED);
        answers.put(published.instance(), published.sid(), 1, Map.of(PLAIN_FIELD, "非常满意"));
    }

    @Test
    void completedJobDownloadsAReadablePdf() throws Exception {
        ExportJobView job = fixture.runToEnd(published.owner(), fixture.create(published.owner(), published, "pdf"));
        byte[] pdf = fixture.download(published.owner(), job);

        assertThat(job.status()).isEqualTo("completed");
        assertThat(job.format()).isEqualTo("pdf");
        try (var document = Loader.loadPDF(pdf)) {
            String text = Normalizer.normalize(new PDFTextStripper().getText(document), Normalizer.Form.NFKC);
            assertThat(text).contains("答卷 1", "Pick exactly one", "非常满意");
        }
    }
}
