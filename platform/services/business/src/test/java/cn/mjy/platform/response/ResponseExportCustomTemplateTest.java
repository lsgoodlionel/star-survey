package cn.mjy.platform.response;

import static cn.mjy.platform.engine.EngineEventType.COMPLETED;
import static cn.mjy.platform.response.ResponseFixture.GENERATION;
import static cn.mjy.platform.response.ResponseFixture.PLAIN_FIELD;
import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import cn.mjy.platform.response.ResponseFixture.Published;
import java.io.ByteArrayInputStream;
import java.io.ByteArrayOutputStream;
import java.nio.charset.StandardCharsets;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.zip.ZipEntry;
import java.util.zip.ZipInputStream;
import java.util.zip.ZipOutputStream;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;

@SpringBootTest
class ResponseExportCustomTemplateTest {

    @Autowired ExportFixture fixture;
    @Autowired FakeResponseAnswerSource answers;
    @Autowired WordTemplateService templates;
    @Autowired ResponseExportService exports;

    private Published published;

    @BeforeEach
    void twoResponses() {
        published = fixture.responses().publishedSurvey();
        for (long id = 1; id <= 2; id++) {
            fixture.responses().response(published.tenant(), published.instance(), published.sid(), GENERATION, id,
                    COMPLETED);
            answers.put(published.instance(), published.sid(), id, Map.of(PLAIN_FIELD, "答案" + id));
        }
    }

    @Test
    void customTemplateIsFrozenAndRenderedOncePerResponse() throws Exception {
        WordTemplateView first = templates.create(published.owner(), published.surveyId(), "报告模板",
                template("第一版 {{response.id}} / {{response.answers}}"));
        String reference = "custom:" + first.id() + ":" + first.version();
        ExportJobView job = exports.create(published.owner(), published.surveyId(),
                new ExportRequest(null, "docx", null, reference), null);

        templates.addVersion(published.owner(), published.surveyId(), first.id(),
                template("第二版 {{response.id}}"));
        ExportJobView completed = fixture.runToEnd(published.owner(), job);
        ExportDownload download = exports.download(published.owner(), completed.jobId());
        byte[] outer;
        try (var input = download.content()) {
            outer = input.readAllBytes();
        }
        Map<String, byte[]> documents = unzip(outer);

        assertThat(download.contentType()).isEqualTo("application/zip");
        assertThat(download.fileName()).endsWith(".docx.zip");
        assertThat(documents).hasSize(2);
        assertThat(documents.values()).allSatisfy(docx ->
                assertThat(String.join("", ExportFixture.docxTexts(docx)))
                        .contains("第一版").doesNotContain("第二版"));
        String allText = documents.values().stream().flatMap(docx -> ExportFixture.docxTexts(docx).stream())
                .reduce("", String::concat);
        assertThat(allText).contains("第一版 1 / Pick exactly one：答案1",
                "第一版 2 / Pick exactly one：答案2");
    }

    @Test
    void idempotencyIncludesTemplateVersionAndCrossTenantReadsAreHidden() throws Exception {
        WordTemplateView first = templates.create(published.owner(), published.surveyId(), "报告模板",
                template("{{response.id}}"));
        WordTemplateView second = templates.addVersion(published.owner(), published.surveyId(), first.id(),
                template("{{response.answers}}"));
        String v1 = "custom:" + first.id() + ":1";
        String v2 = "custom:" + first.id() + ":" + second.version();
        ExportRequest request = new ExportRequest(null, "docx", null, v1);

        ExportJobView created = exports.create(published.owner(), published.surveyId(), request, "same-key");
        assertThat(exports.create(published.owner(), published.surveyId(), request, "same-key").jobId())
                .isEqualTo(created.jobId());
        assertThatThrownBy(() -> exports.create(published.owner(), published.surveyId(),
                new ExportRequest(null, "docx", null, v2), "same-key"))
                .isInstanceOf(ExportConflictException.class);

        Published otherTenant = fixture.responses().publishedSurvey();
        assertThatThrownBy(() -> templates.get(otherTenant.owner(), published.surveyId(), first.id(), 1))
                .isInstanceOf(ResponseNotFoundException.class);
        assertThatThrownBy(() -> exports.create(published.owner(), published.surveyId(),
                new ExportRequest(null, "pdf", null, v1), null))
                .isInstanceOf(InvalidResponseQueryException.class);
    }

    private static byte[] template(String text) throws Exception {
        ByteArrayOutputStream output = new ByteArrayOutputStream();
        try (ZipOutputStream zip = new ZipOutputStream(output)) {
            entry(zip, "[Content_Types].xml", "<Types/>");
            entry(zip, "_rels/.rels", "<Relationships/>");
            entry(zip, "word/document.xml", "<w:document xmlns:w=\"http://schemas.openxmlformats.org/wordprocessingml/2006/main\"><w:body><w:p><w:r><w:t>"
                    + text + "</w:t></w:r></w:p></w:body></w:document>");
        }
        return output.toByteArray();
    }

    private static void entry(ZipOutputStream zip, String name, String value) throws Exception {
        zip.putNextEntry(new ZipEntry(name));
        zip.write(value.getBytes(StandardCharsets.UTF_8));
        zip.closeEntry();
    }

    private static Map<String, byte[]> unzip(byte[] value) throws Exception {
        Map<String, byte[]> files = new LinkedHashMap<>();
        try (ZipInputStream zip = new ZipInputStream(new ByteArrayInputStream(value))) {
            ZipEntry entry;
            while ((entry = zip.getNextEntry()) != null) {
                files.put(entry.getName(), zip.readAllBytes());
            }
        }
        return files;
    }
}
