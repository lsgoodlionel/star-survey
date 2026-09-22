package cn.mjy.platform.response;

import static cn.mjy.platform.engine.EngineEventType.COMPLETED;
import static cn.mjy.platform.response.ResponseFixture.GENERATION;
import static cn.mjy.platform.response.ResponseFixture.PLAIN_FIELD;
import static cn.mjy.platform.response.ResponseFixture.SENSITIVE_FIELD;
import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import cn.mjy.platform.access.ResponseFieldPolicy;
import cn.mjy.platform.response.ExportFixture.Member;
import cn.mjy.platform.response.ResponseFixture.Published;
import java.util.List;
import java.util.Map;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;

/**
 * 导出的权限：统计查看者不能建导出；敏感列按字段策略遮蔽；下载时再次授权（撤权后即使作业 id 有效也 403）；
 * 导出进行中撤权，作业失败而不是生成一份"撤权后的"文件。
 */
@SpringBootTest
class ResponseExportAccessTest {

    @Autowired
    private ExportFixture fixture;

    @Autowired
    private FakeResponseAnswerSource answers;

    @Autowired
    private ResponseExportService exports;

    private Published p;

    @BeforeEach
    void aPublishedSurveyWithTwoResponses() {
        p = fixture.responses().publishedSurvey();
        for (long id = 1; id <= 2; id++) {
            fixture.responses().response(p.tenant(), p.instance(), p.sid(), GENERATION, id, COMPLETED);
            answers.put(p.instance(), p.sid(), id, Map.of(PLAIN_FIELD, "A1", SENSITIVE_FIELD, "1381234567" + id));
        }
    }

    @Test
    void aStatisticsViewerCannotCreateAnExport() {
        Member stats = fixture.member(p, "stats", "statistics_viewer");

        assertThatThrownBy(() -> fixture.create(stats.ctx(), p, "csv"))
                .isInstanceOf(ResponseAccessDeniedException.class);
    }

    @Test
    void aManagerWithoutTheSensitivePermissionGetsMaskedColumns() {
        Member manager = fixture.member(p, "pm", "project_manager");

        ExportJobView job = fixture.runToEnd(manager.ctx(), fixture.create(manager.ctx(), p, "csv"));
        List<List<String>> rows = ExportFixture.unzipCsv(fixture.download(manager.ctx(), job)).get("responses.csv");

        assertThat(job.status()).isEqualTo("completed");
        assertThat(job.sensitiveRevealed()).isFalse();
        int text = rows.get(0).indexOf("QTEXT");
        int single = rows.get(0).indexOf("QSINGLE");
        assertThat(rows.subList(2, rows.size())).hasSize(2).allSatisfy(row -> {
            assertThat(row.get(text)).isEqualTo(ResponseFieldPolicy.MASK);
            assertThat(row.get(single)).isEqualTo("A1");
        });
        List<List<String>> dictionary = ExportFixture.unzipCsv(fixture.download(manager.ctx(), job)).get("fields.csv");
        assertThat(dictionary).anySatisfy(row -> assertThat(row).contains("QTEXT", "true", "masked"));
    }

    @Test
    void aRawDataViewerSeesSensitivePlaintext() {
        Member raw = fixture.member(p, "raw", "raw_data_viewer");

        ExportJobView job = fixture.runToEnd(raw.ctx(), fixture.create(raw.ctx(), p, "csv"));
        List<List<String>> rows = ExportFixture.unzipCsv(fixture.download(raw.ctx(), job)).get("responses.csv");

        assertThat(job.sensitiveRevealed()).isTrue();
        int text = rows.get(0).indexOf("QTEXT");
        assertThat(rows.get(2).get(text)).isEqualTo("13812345671");
    }

    @Test
    void revokingAfterCompletionMakesTheDownload403EvenWithAValidJobId() {
        Member raw = fixture.member(p, "raw", "raw_data_viewer");
        ExportJobView job = fixture.runToEnd(raw.ctx(), fixture.create(raw.ctx(), p, "xlsx"));
        assertThat(job.status()).isEqualTo("completed");

        fixture.revoke(p, raw);

        assertThatThrownBy(() -> exports.download(raw.ctx(), job.jobId()))
                .isInstanceOf(ResponseAccessDeniedException.class);
    }

    @Test
    void losingOnlyTheSensitivePermissionBlocksDownloadingAFileWithPlaintext() {
        Member raw = fixture.member(p, "raw", "raw_data_viewer");
        fixture.grant(p, raw.ctx(), "project_manager");
        ExportJobView plaintext = fixture.runToEnd(raw.ctx(), fixture.create(raw.ctx(), p, "csv"));

        fixture.revoke(p, raw);
        ExportJobView masked = fixture.runToEnd(raw.ctx(), fixture.create(raw.ctx(), p, "csv"));

        assertThat(plaintext.sensitiveRevealed()).isTrue();
        assertThatThrownBy(() -> exports.download(raw.ctx(), plaintext.jobId()))
                .isInstanceOf(ResponseAccessDeniedException.class);
        assertThat(masked.sensitiveRevealed()).isFalse();
        assertThat(fixture.download(raw.ctx(), masked)).isNotEmpty();
    }

    @Test
    void revokingWhileTheExportRunsFailsTheJob() {
        Member raw = fixture.member(p, "raw", "raw_data_viewer");
        ExportJobView job = fixture.create(raw.ctx(), p, "csv");

        fixture.revoke(p, raw);
        fixture.runToEnd(raw.ctx(), job);

        ExportJobView seen = exports.status(raw.ctx(), job.jobId());
        assertThat(seen.status()).isEqualTo("failed");
        assertThat(seen.error()).isEqualTo("permission_changed");
    }

    @Test
    void anotherMemberOfTheSameTenantCannotSeeOrDownloadSomeoneElsesJob() {
        Member raw = fixture.member(p, "raw", "raw_data_viewer");
        Member other = fixture.member(p, "raw2", "raw_data_viewer");
        ExportJobView job = fixture.runToEnd(raw.ctx(), fixture.create(raw.ctx(), p, "csv"));

        assertThatThrownBy(() -> exports.status(other.ctx(), job.jobId()))
                .isInstanceOf(ResponseNotFoundException.class);
        assertThatThrownBy(() -> exports.download(other.ctx(), job.jobId()))
                .isInstanceOf(ResponseNotFoundException.class);
        assertThatThrownBy(() -> exports.cancel(other.ctx(), job.jobId()))
                .isInstanceOf(ResponseNotFoundException.class);
    }
}
