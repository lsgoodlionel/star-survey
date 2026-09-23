package cn.mjy.platform.response;

import static cn.mjy.platform.engine.EngineEventType.COMPLETED;
import static cn.mjy.platform.engine.EngineEventType.DELETED;
import static cn.mjy.platform.response.ResponseFixture.GENERATION;
import static cn.mjy.platform.response.ResponseFixture.PLAIN_FIELD;
import static cn.mjy.platform.response.ResponseFixture.SENSITIVE_FIELD;
import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import cn.mjy.platform.response.FakeResponseAnswerSource.SimulatedCrash;
import cn.mjy.platform.response.ResponseFixture.Published;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.util.Map;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.test.context.TestPropertySource;

/**
 * 可恢复：进程在批次中途死掉（租约未释放、下一个分片写了一半），租约超时后从检查点接着做，
 * 最终文件与一次跑完的作业逐字节相同；网关暂时不可达时作业退避重试，同样得到相同文件。
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

    private Published p;

    @BeforeEach
    void fiftyResponsesWithAnswersAndOneDeleted() {
        p = fixture.responses().publishedSurvey();
        for (long id = 1; id <= RESPONSES; id++) {
            fixture.responses().response(p.tenant(), p.instance(), p.sid(), GENERATION, id,
                    id == 13 ? DELETED : COMPLETED);
            answers.put(p.instance(), p.sid(), id, Map.of(PLAIN_FIELD, "A" + (id % 3),
                    SENSITIVE_FIELD, "=HYPERLINK(\"http://169.254.169.254/\")" + id));
        }
    }

    @Test
    void aCrashMidJobResumesFromTheCheckpointAndProducesAnIdenticalFile() throws IOException {
        for (String format : new String[] {"csv", "xlsx", "sav"}) {
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
