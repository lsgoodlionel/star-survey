package cn.mjy.platform.response;

import static cn.mjy.platform.engine.EngineEventType.COMPLETED;
import static cn.mjy.platform.engine.EngineEventType.DELETED;
import static cn.mjy.platform.response.ExportFixture.UPLOAD_FIELD;
import static cn.mjy.platform.response.ResponseFixture.GENERATION;
import static cn.mjy.platform.response.ResponseFixture.PLAIN_FIELD;
import static cn.mjy.platform.response.ResponseFixture.SENSITIVE_FIELD;
import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import cn.mjy.platform.response.AnswerBatch.ExtensionAnswer;
import cn.mjy.platform.response.FakeResponseAnswerSource.SimulatedCrash;
import cn.mjy.platform.response.ResponseFixture.Published;
import java.io.ByteArrayInputStream;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.zip.ZipEntry;
import java.util.zip.ZipInputStream;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.test.context.TestPropertySource;

/**
 * 可恢复：进程在批次中途死掉（租约未释放、下一个分片写了一半），租约超时后从检查点接着做，
 * 最终文件与一次跑完的作业逐字节相同；网关暂时不可达时作业退避重试，同样得到相同文件。
 *
 * <p>场景里带扩展副表作答，且各份答卷的行数<b>不同</b>：逐字节相同的断言因此也覆盖长度不定的那张表
 * （ADR 0015 增补二）。还带<b>附件</b>，且每份答卷的附件数不同、有一部分引擎里已经取不到：
 * 逐字节相同的断言因此也覆盖附件包——清单、打包结果索引与文件本身（ADR 0015 增补四）。
 */
@SpringBootTest
@TestPropertySource(properties = "platform.response.export.batch-size=7")
class ResponseExportResumeTest {

    private static final int RESPONSES = 50;

    @Autowired
    private ExportFixture fixture;

    @Autowired
    private FakeResponseAnswerSource answers;

    @Autowired
    private ResponseExportWorker worker;

    @Autowired
    private ResponseExportService exports;

    @Autowired
    private ExportFileStore files;

    @Autowired
    private FakeResponseAttachmentSource attachments;

    private Published p;

    @BeforeEach
    void fiftyResponsesWithAnswersAttachmentsAndOneDeleted() {
        p = fixture.responses().publishedSurvey(fixture.responses().extensionDefinition());
        fixture.addUploadColumn(p, 1);
        for (long id = 1; id <= RESPONSES; id++) {
            fixture.responses().response(p.tenant(), p.instance(), p.sid(), GENERATION, id,
                    id == 13 ? DELETED : COMPLETED);
            answers.put(p.instance(), p.sid(), id, Map.of(PLAIN_FIELD, "A" + (id % 3),
                    SENSITIVE_FIELD, "=HYPERLINK(\"http://169.254.169.254/\")" + id,
                    UPLOAD_FIELD, uploads(id)));
            answers.putExtension(p.instance(), p.sid(), id, "QTABLE", sideTable(id));
            storeAttachments(id);
        }
    }

    /** 附件数逐份答卷不同（0–2 份）：包里的条目数不与答卷数成比例。 */
    private static String uploads(long responseId) {
        int count = (int) (responseId % 3);
        if (count == 0) {
            return "";
        }
        StringBuilder files = new StringBuilder("[");
        for (int i = 1; i <= count; i++) {
            files.append(i > 1 ? "," : "")
                    .append("{\"name\":\"甲乙.png\",\"size\":\"3\",\"ext\":\"png\",\"filename\":\"")
                    .append(storedName(responseId, i)).append("\"}");
        }
        return files.append(']').toString();
    }

    private static String storedName(long responseId, int index) {
        return "fu_" + responseId + "_" + index;
    }

    /** 每 5 份里有一份引擎里已经没有了：包里必须有 absent 那一档，不能只有取到的。 */
    private void storeAttachments(long responseId) {
        if (responseId % 5 == 0) {
            return;
        }
        for (int i = 1; i <= responseId % 3; i++) {
            attachments.put(p.instance(), p.sid(), responseId, storedName(responseId, i),
                    "内容 " + responseId + "-" + i);
        }
    }

    /** 行数逐份答卷不同（0–3 行），且第 7 份没通过插件闸门：长表的行数不再与答卷数成比例。 */
    private static ExtensionAnswer sideTable(long responseId) {
        if (responseId % 7 == 0) {
            return new ExtensionAnswer("rt1", false, List.of());
        }
        List<Map<String, String>> rows = new java.util.ArrayList<>();
        for (int index = 0; index < responseId % 4; index++) {
            rows.add(Map.of("item", "物品" + index, "qty", Integer.toString(index + 1)));
        }
        return new ExtensionAnswer("rt1", true, List.copyOf(rows));
    }

    @Test
    void aCrashMidJobResumesFromTheCheckpointAndProducesAnIdenticalFile() throws IOException {
        for (String format : new String[] {"csv", "xlsx", "sav", "docx", "attachments"}) {
            ExportJobView reference = fixture.create(p.owner(), p, format);
            ExportJobView crashed = fixture.create(p.owner(), p, format);
            byte[] expected = fixture.download(p.owner(), fixture.runToEnd(p.owner(), reference));

            answers.crashAfter(p.instance(), 3);
            assertThatThrownBy(() -> worker.process(p.tenant(), crashed.jobId(), Integer.MAX_VALUE))
                    .isInstanceOf(SimulatedCrash.class);
            ExportJobView midway = exports.status(p.owner(), crashed.jobId());
            assertThat(midway.status()).isEqualTo("running");
            assertThat(midway.processedRows()).isEqualTo(21);
            // 死掉之前下一个分片已经落盘、但检查点没来得及前移（内容还是坏的）：恢复时必须被整体覆盖。
            try (ExportFileStore.Upload junk = files.create(
                    ResponseExportWorker.partKey(p.tenant(), crashed.jobId(), 22))) {
                junk.stream().write("[\"half a row".getBytes(StandardCharsets.UTF_8));
                junk.commit();
            }

            assertThat(worker.process(p.tenant(), crashed.jobId(), Integer.MAX_VALUE))
                    .as("lease still held by the dead worker").isFalse();
            fixture.expireLease(p.tenant(), crashed.jobId());
            ExportJobView resumed = fixture.runToEnd(p.owner(), crashed);

            byte[] actual = fixture.download(p.owner(), resumed);
            assertThat(resumed.status()).isEqualTo("completed");
            assertThat(ExportFixture.sha256(actual)).as(format).isEqualTo(ExportFixture.sha256(expected));
            assertThat(resumed.sha256()).isEqualTo(ExportFixture.sha256(actual));
            assertThat(resumed.fileSize()).isEqualTo(actual.length);
        }
    }

    @Test
    void aTransientGatewayFailureIsRetriedWithoutDuplicatingRows() {
        ExportJobView reference = fixture.create(p.owner(), p, "csv");
        ExportJobView retried = fixture.create(p.owner(), p, "csv");
        byte[] expected = fixture.download(p.owner(), fixture.runToEnd(p.owner(), reference));

        answers.failFor(p.instance());
        ExportJobView failedOnce = fixture.runToEnd(p.owner(), retried);
        answers.recover(p.instance());
        ExportJobView done = fixture.runToEnd(p.owner(), retried);

        assertThat(failedOnce.status()).isIn("queued", "running");
        assertThat(failedOnce.attempts()).isEqualTo(1);
        assertThat(failedOnce.error()).isEqualTo("answers_unavailable");
        assertThat(done.status()).isEqualTo("completed");
        assertThat(fixture.download(p.owner(), done)).isEqualTo(expected);
    }

    @Test
    void theCsvGuardsAgainstFormulaInjectionAndKeepsDeletedRowsWithoutAnswers() {
        ExportJobView job = fixture.runToEnd(p.owner(), fixture.create(p.owner(), p, "csv"));

        var rows = ExportFixture.unzipCsv(fixture.download(p.owner(), job)).get("responses.csv");
        int text = rows.get(0).indexOf("QTEXT");
        int status = rows.get(0).indexOf("answersstatus");
        assertThat(rows).hasSize(RESPONSES + 2);
        assertThat(rows.get(2).get(text)).startsWith("'=HYPERLINK(");
        assertThat(rows.get(2 + 12).get(status)).isEqualTo("deleted");
        assertThat(rows.get(2 + 12).get(text)).isEmpty();
    }

    /**
     * 逐字节相同的断言只有在那张表确实有内容时才说明问题：一张空表在两次跑法里都是空的，
     * 照样"相同"。这里钉住它确实有内容，且行数与答卷数不成比例（行数逐份答卷不同）。
     */
    @Test
    void theVariableLengthSideTableSheetIsReallyPartOfTheReproducedFile() {
        ExportJobView job = fixture.runToEnd(p.owner(), fixture.create(p.owner(), p, "csv"));

        var rows = ExportFixture.unzipCsv(fixture.download(p.owner(), job)).get("extensions.csv");

        assertThat(rows.get(0)).containsExactly(ExportLayout.EXTENSION_HEADER.toArray(String[]::new));
        assertThat(rows).hasSizeGreaterThan(RESPONSES);
        assertThat(rows.subList(1, rows.size())).anySatisfy(row -> assertThat(row.get(7)).isEqualTo("2"))
                .anySatisfy(row -> assertThat(row.get(6)).isEqualTo("false"));
    }

    /**
     * 与上一条同理：一个空包在两次跑法里都是空的，照样"相同"。这里钉住附件包确实有内容——
     * 既有真正进了包的文件条目，也有如实记成缺失的那一档。
     */
    @Test
    void theAttachmentPackageIsReallyPartOfTheReproducedFile() throws IOException {
        byte[] pack = fixture.download(p.owner(),
                fixture.runToEnd(p.owner(), fixture.create(p.owner(), p, "attachments")));

        List<String> names = entryNames(pack);
        byte[] indexBytes = entry(pack, AttachmentsExportWriter.INDEX_ENTRY);
        List<List<String>> index = ExportFixture.parseCsv(
                new String(indexBytes, 3, indexBytes.length - 3, StandardCharsets.UTF_8));

        assertThat(names).filteredOn(name -> name.startsWith("files/")).hasSizeGreaterThan(20);
        assertThat(index.subList(1, index.size()))
                .anySatisfy(row -> assertThat(row).contains("stored"))
                .anySatisfy(row -> assertThat(row).contains(ExportAttachments.REASON_NOT_FOUND));
    }

    private static List<String> entryNames(byte[] zip) throws IOException {
        List<String> names = new ArrayList<>();
        try (ZipInputStream in = new ZipInputStream(new ByteArrayInputStream(zip))) {
            ZipEntry entry;
            while ((entry = in.getNextEntry()) != null) {
                names.add(entry.getName());
            }
        }
        return names;
    }

    private static byte[] entry(byte[] zip, String name) throws IOException {
        try (ZipInputStream in = new ZipInputStream(new ByteArrayInputStream(zip))) {
            ZipEntry entry;
            while ((entry = in.getNextEntry()) != null) {
                if (name.equals(entry.getName())) {
                    return in.readAllBytes();
                }
            }
        }
        throw new IllegalStateException("no entry " + name);
    }

    @Test
    void aCancelledJobStopsAndCannotBeDownloaded() {
        ExportJobView job = fixture.create(p.owner(), p, "csv");
        worker.process(p.tenant(), job.jobId(), 2);

        ExportJobView cancelled = exports.cancel(p.owner(), job.jobId());
        fixture.expireLease(p.tenant(), job.jobId());
        worker.process(p.tenant(), job.jobId(), Integer.MAX_VALUE);

        assertThat(cancelled.status()).isEqualTo("cancelled");
        assertThat(exports.status(p.owner(), job.jobId()).status()).isEqualTo("cancelled");
        assertThatThrownBy(() -> exports.download(p.owner(), job.jobId()))
                .isInstanceOf(ExportNotReadyException.class);
    }

    @Test
    void anExpiredJobLosesItsFileAndDownloadIsGone() {
        ExportJobView job = fixture.runToEnd(p.owner(), fixture.create(p.owner(), p, "csv"));
        fixture.expireJob(p.tenant(), job.jobId());

        worker.expireDue();

        assertThat(exports.status(p.owner(), job.jobId()).status()).isEqualTo("expired");
        assertThatThrownBy(() -> exports.download(p.owner(), job.jobId()))
                .isInstanceOf(ExportGoneException.class);
        assertThat(files.exists(ResponseExportWorker.fileKey(p.tenant(), job.jobId(), "zip"))).isFalse();
    }
}
