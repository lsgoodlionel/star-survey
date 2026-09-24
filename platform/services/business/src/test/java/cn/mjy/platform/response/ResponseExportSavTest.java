package cn.mjy.platform.response;

import static cn.mjy.platform.engine.EngineEventType.COMPLETED;
import static cn.mjy.platform.response.ResponseFixture.GENERATION;
import static cn.mjy.platform.response.ResponseFixture.PLAIN_FIELD;
import static cn.mjy.platform.response.ResponseFixture.SENSITIVE_FIELD;
import static org.assertj.core.api.Assertions.assertThat;

import cn.mjy.platform.response.ResponseFixture.Published;
import java.io.ByteArrayInputStream;
import java.io.IOException;
import java.io.UncheckedIOException;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.zip.ZipEntry;
import java.util.zip.ZipInputStream;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;

/**
 * 端到端走一遍导出作业，产出 SPSS 包（R06-03）：作业能建、能跑完、能下载，包里的 responses.sav
 * 带着变量标签与值标签，答卷号按数值变量导出，看起来像公式的文本照样被挡住。
 */
@SpringBootTest
class ResponseExportSavTest {

    private static final String FORMULA = "=1+1";

    @Autowired
    private ExportFixture fixture;

    @Autowired
    private FakeResponseAnswerSource answers;

    private Published p;

    @BeforeEach
    void aPublishedSurveyWithOneCompletedResponse() {
        p = fixture.responses().publishedSurvey();
        fixture.responses().response(p.tenant(), p.instance(), p.sid(), GENERATION, 1, COMPLETED);
        answers.put(p.instance(), p.sid(), 1, Map.of(PLAIN_FIELD, "A1", SENSITIVE_FIELD, FORMULA));
    }

    private Map<String, byte[]> runAndDownload() {
        ExportJobView job = fixture.runToEnd(p.owner(), fixture.create(p.owner(), p, "sav"));
        assertThat(job.status()).isEqualTo("completed");
        return unzip(fixture.download(p.owner(), job));
    }

    @Test
    void theBundleHasTheDatasetItsVariableDictionaryAndTheUsualSideSheets() {
        Map<String, byte[]> entries = runAndDownload();

        assertThat(entries.keySet())
                .containsExactly("responses.sav", "variables.csv", "fields.csv", "attachments.csv");
        List<List<String>> variables = ExportFixture.parseCsv(utf8(entries.get("variables.csv")));
        assertThat(variables.get(0)).containsExactly("name", "code", "label", "measure", "width", "valuelabels");
        assertThat(variables).anySatisfy(row -> assertThat(row)
                .containsSequence("QSINGLE", "QSINGLE", "Pick exactly one", "string"));
        assertThat(variables).anySatisfy(row -> assertThat(row).contains("A1=First; A2=Second; A3=Third"));
    }

    @Test
    void theDatasetCarriesTheAnswersWithVariableAndValueLabels() {
        SavReader.Sav sav = SavReader.read(runAndDownload().get("responses.sav"));

        assertThat(sav.caseCount()).isEqualTo(1);
        assertThat(sav.cell(0, "responseid")).as("答卷号是数值变量").isEqualTo(1.0d);
        assertThat(sav.cell(0, "QSINGLE")).isEqualTo("A1");
        assertThat(sav.variable(sav.shortNameOf("QSINGLE")).label()).isEqualTo("Pick exactly one");
        assertThat(sav.variable(sav.shortNameOf("QSINGLE")).valueLabels())
                .contains(new SavReader.ValueLabel("A1", "First"), new SavReader.ValueLabel("A3", "Third"));
        assertThat(sav.variable(sav.shortNameOf("state")).valueLabels())
                .as("状态代码宽于 8 字节，SPSS 挂不了值标签；完整取值在 variables.csv 里").isEmpty();
    }

    @Test
    void enumeratedColumnsTooWideForSpssValueLabelsAreStillDocumentedInTheVariableDictionary() {
        List<List<String>> variables = ExportFixture.parseCsv(utf8(runAndDownload().get("variables.csv")));

        assertThat(variables).anySatisfy(row -> assertThat(row).containsSequence("state", "state", "状态")
                .last().asString().contains("engine_completed=已完成", "deleted=已删除"));
        assertThat(variables).anySatisfy(row -> assertThat(row)
                .containsSequence("answersstatus", "answersstatus", "作答状态")
                .last().asString().contains("available=已取到作答", "archived=已被替换的代次"));
    }

    @Test
    void formulaLookingAnswersAreGuardedInTheDatasetToo() {
        assertThat(SavReader.read(runAndDownload().get("responses.sav")).cell(0, "QTEXT"))
                .isEqualTo("'" + FORMULA);
    }

    @Test
    void theSpssFormatIsOfferedAsAZipBundleNamedAfterIt() {
        assertThat(ExportFormat.fromCode("sav")).contains(ExportFormat.SAV);
        assertThat(ExportFormat.SAV.extension()).isEqualTo("sav.zip");
        assertThat(ExportFormat.SAV.contentType()).isEqualTo("application/zip");
    }

    private static String utf8(byte[] bytes) {
        return new String(bytes, 3, bytes.length - 3, java.nio.charset.StandardCharsets.UTF_8);
    }

    private static Map<String, byte[]> unzip(byte[] zip) {
        Map<String, byte[]> entries = new LinkedHashMap<>();
        try (ZipInputStream in = new ZipInputStream(new ByteArrayInputStream(zip))) {
            ZipEntry entry;
            while ((entry = in.getNextEntry()) != null) {
                entries.put(entry.getName(), in.readAllBytes());
            }
        } catch (IOException e) {
            throw new UncheckedIOException(e);
        }
        return entries;
    }
}
