package cn.mjy.platform.response;

import static cn.mjy.platform.engine.EngineEventType.COMPLETED;
import static cn.mjy.platform.response.ExportFixture.UPLOAD_FIELD;
import static cn.mjy.platform.response.ExportFixture.URL_FIELD;
import static cn.mjy.platform.response.ResponseFixture.GENERATION;
import static cn.mjy.platform.response.ResponseFixture.PLAIN_FIELD;
import static org.assertj.core.api.Assertions.assertThat;

import cn.mjy.platform.response.ResponseFixture.Published;
import java.util.List;
import java.util.Map;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;

/**
 * 多版本联合导出与附件清单：两个版本的列按列代码合并成一张表；上传题的文件逐个进清单；
 * 答卷里的内网 / 元数据地址只作为文本原样输出（导出从不发起任何网络请求）。
 */
@SpringBootTest
class ResponseExportAttachmentTest {

    private static final String METADATA_URL = "http://169.254.169.254/latest/meta-data/";

    @Autowired
    private ExportFixture fixture;

    @Autowired
    private FakeResponseAnswerSource answers;

    @Test
    void versionsAreMergedAndUploadsAreListedWithoutFetchingUrls() {
        Published p = fixture.responses().publishedSurvey();
        long sid2 = fixture.addUploadVersion(p);
        fixture.responses().response(p.tenant(), p.instance(), p.sid(), GENERATION, 1, COMPLETED);
        answers.put(p.instance(), p.sid(), 1, Map.of(PLAIN_FIELD, "A2"));
        fixture.responses().response(p.tenant(), p.instance(), sid2, GENERATION, 7, COMPLETED);
        answers.put(p.instance(), sid2, 7, Map.of(
                UPLOAD_FIELD, "[{\"name\":\"a.png\",\"size\":\"3.2\",\"ext\":\"png\",\"filename\":\"fu_1\"},"
                        + "{\"name\":\"b.pdf\",\"size\":\"10\",\"ext\":\"pdf\",\"filename\":\"fu_2\"}]",
                URL_FIELD, METADATA_URL));

        ExportJobView job = fixture.runToEnd(p.owner(), fixture.create(p.owner(), p, "csv"));
        Map<String, List<List<String>>> files = ExportFixture.unzipCsv(fixture.download(p.owner(), job));

        List<List<String>> rows = files.get("responses.csv");
        List<String> header = rows.get(0);
        assertThat(header).contains("version", "sid", "responseid", "QSINGLE", "QTEXT", "QFILE", "QURL");
        assertThat(rows).hasSize(4);
        assertThat(rows.get(2).get(header.indexOf("QSINGLE"))).isEqualTo("A2");
        assertThat(rows.get(2).get(header.indexOf("QURL"))).isEmpty();
        assertThat(rows.get(3).get(header.indexOf("version"))).isEqualTo("2");
        assertThat(rows.get(3).get(header.indexOf("QURL"))).isEqualTo(METADATA_URL);
        assertThat(rows.get(3).get(header.indexOf("QSINGLE"))).isEmpty();

        List<List<String>> attachments = files.get("attachments.csv");
        assertThat(attachments).hasSize(3);
        assertThat(attachments.get(1)).containsSequence("7", "QFILE", UPLOAD_FIELD, "1", "a.png", "3.2", "png", "fu_1");
        assertThat(attachments.get(2)).containsSequence("7", "QFILE", UPLOAD_FIELD, "2", "b.pdf");

        List<List<String>> dictionary = files.get("fields.csv");
        assertThat(dictionary).anySatisfy(row -> assertThat(row).startsWith("2", Long.toString(sid2), "QFILE"));
    }
}
