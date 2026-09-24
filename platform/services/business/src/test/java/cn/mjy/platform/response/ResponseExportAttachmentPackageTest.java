package cn.mjy.platform.response;

import static cn.mjy.platform.engine.EngineEventType.COMPLETED;
import static cn.mjy.platform.response.ExportFixture.UPLOAD_FIELD;
import static cn.mjy.platform.response.ResponseFixture.GENERATION;
import static org.assertj.core.api.Assertions.assertThat;

import cn.mjy.platform.response.ResponseFixture.Published;
import java.io.ByteArrayInputStream;
import java.io.IOException;
import java.io.UncheckedIOException;
import java.nio.charset.StandardCharsets;
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
 * 整条作业链路的附件包（R06-07，ADR 0015 增补四）：建作业 → 后台分批取件 → 下载。
 * 包里有清单、打包结果索引与文件本身；引擎里已经没有的那一份如实记成缺失，不拖垮整包。
 */
@SpringBootTest
class ResponseExportAttachmentPackageTest {

    private static final String THREE_FILES = "[{\"name\":\"甲.png\",\"size\":\"3.2\",\"ext\":\"png\","
            + "\"filename\":\"fu_1\"},{\"name\":\"乙.pdf\",\"size\":\"10\",\"ext\":\"pdf\",\"filename\":\"fu_2\"},"
            + "{\"name\":\"丙.txt\",\"size\":\"1\",\"ext\":\"txt\",\"filename\":\"fu_3\"}]";

    @Autowired
    private ExportFixture fixture;

    @Autowired
    private FakeResponseAnswerSource answers;

    @Autowired
    private FakeResponseAttachmentSource attachments;

    @Autowired
    private ResponseExportWorker worker;

    @Autowired
    private ResponseExportService exports;

    private Published p;
    private long uploadSid;

    @BeforeEach
    void oneResponseWithThreeUploadsOfWhichOneIsGone() {
        p = fixture.responses().publishedSurvey();
        uploadSid = fixture.addUploadVersion(p);
        fixture.responses().response(p.tenant(), p.instance(), uploadSid, GENERATION, 7, COMPLETED);
        answers.put(p.instance(), uploadSid, 7, Map.of(UPLOAD_FIELD, THREE_FILES));
        attachments.put(p.instance(), uploadSid, 7, "fu_1", "甲的内容");
        // fu_2 引擎里已经没有了（作答后被清理）。
        attachments.put(p.instance(), uploadSid, 7, "fu_3", "丙");
        attachments.clearCalls();
    }

    private Map<String, byte[]> unzip(byte[] zip) {
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

    private static List<List<String>> csv(byte[] bytes) {
        return ExportFixture.parseCsv(new String(bytes, 3, bytes.length - 3, StandardCharsets.UTF_8));
    }

    private byte[] pack() {
        return fixture.download(p.owner(), fixture.runToEnd(p.owner(), fixture.create(p.owner(), p, "attachments")));
    }

    @Test
    void thePackageCarriesTheFilesThemselvesAlongsideTheManifest() {
        Map<String, byte[]> entries = unzip(pack());

        assertThat(entries).containsKey(AttachmentsExportWriter.MANIFEST_ENTRY);
        String first = "files/" + uploadSid + "-7/" + UPLOAD_FIELD + "-1-fu_1";
        String third = "files/" + uploadSid + "-7/" + UPLOAD_FIELD + "-3-fu_3";
        assertThat(entries.get(first)).asString(StandardCharsets.UTF_8).isEqualTo("甲的内容");
        assertThat(entries.get(third)).asString(StandardCharsets.UTF_8).isEqualTo("丙");
        assertThat(entries).doesNotContainKey("files/" + uploadSid + "-7/" + UPLOAD_FIELD + "-2-fu_2");
    }

    /** 一份取不到不让整包失败：索引如实写 absent，其余两份照常在。 */
    @Test
    void anAttachmentTheEngineNoLongerHasIsReportedRatherThanFailingTheJob() {
        List<List<String>> index = csv(unzip(pack()).get(AttachmentsExportWriter.INDEX_ENTRY));

        assertThat(index.get(0)).containsExactlyElementsOf(AttachmentsExportWriter.INDEX_HEADER);
        assertThat(index).hasSize(4);
        assertThat(index.get(1)).contains("stored", "甲.png").doesNotContain("not_found");
        assertThat(index.get(2)).contains("absent", ExportAttachments.REASON_NOT_FOUND, "乙.pdf");
        assertThat(index.get(3)).contains("stored", "丙.txt");
    }

    /** 清单本身与 CSV 包里的 attachments.csv 逐字节同构：换个包不等于换套口径。 */
    @Test
    void theManifestIsTheSameOneTheOtherPackagesCarry() {
        byte[] fromCsvPackage = fixture.download(p.owner(),
                fixture.runToEnd(p.owner(), fixture.create(p.owner(), p, "csv")));

        assertThat(unzip(pack()).get(AttachmentsExportWriter.MANIFEST_ENTRY))
                .isEqualTo(unzip(fromCsvPackage).get("attachments.csv"));
    }

    /** 取件按份数调用，一份一次；取不到的那一份也只问一次（之后有墓碑）。 */
    @Test
    void eachAttachmentIsFetchedOnceAndOnlyByItsStoredName() {
        pack();

        List<AttachmentQuery> calls = attachments.calls();
        assertThat(calls).hasSize(3);
        assertThat(calls).extracting(AttachmentQuery::storedName).containsExactly("fu_1", "fu_2", "fu_3");
        assertThat(calls).allSatisfy(call -> {
            assertThat(call.generation()).isEqualTo(GENERATION);
            assertThat(call.engineSid()).isEqualTo(uploadSid);
            assertThat(call.fieldname()).isEqualTo(UPLOAD_FIELD);
        });
    }

    /** 网关暂时不可达：作业退避重试，恢复后只补没取到的那些，最终包与一次跑完的相同。 */
    @Test
    void aTransientFailureRetriesAndOnlyRefetchesWhatIsStillMissing() {
        byte[] expected = pack();
        attachments.clearCalls();

        ExportJobView retried = fixture.create(p.owner(), p, "attachments");
        attachments.failFor(p.instance());
        ExportJobView failedOnce = fixture.runToEnd(p.owner(), retried);
        attachments.recover(p.instance());
        ExportJobView done = fixture.runToEnd(p.owner(), retried);

        assertThat(failedOnce.status()).isIn("queued", "running");
        assertThat(failedOnce.error()).isEqualTo("attachments_unavailable");
        assertThat(done.status()).isEqualTo("completed");
        assertThat(fixture.download(p.owner(), done)).isEqualTo(expected);
    }

    /** 其余格式不取字节：只出清单，一次取件都不发生。 */
    @Test
    void theOtherFormatsStillOnlyListAttachmentsAndNeverFetchBytes() {
        fixture.runToEnd(p.owner(), fixture.create(p.owner(), p, "csv"));
        fixture.runToEnd(p.owner(), fixture.create(p.owner(), p, "xlsx"));

        assertThat(attachments.calls()).isEmpty();
    }

    /** 下载的仍然是一个 ZIP，内容类型与文件名照旧由格式决定。 */
    @Test
    void theDownloadIsAZipLikeEveryOtherPackage() {
        ExportJobView job = fixture.runToEnd(p.owner(), fixture.create(p.owner(), p, "attachments"));

        ExportDownload download = exports.download(p.owner(), job.jobId());
        assertThat(download.contentType()).isEqualTo("application/zip");
        assertThat(download.fileName()).endsWith(".zip");
        assertThat(worker.expireDue()).isNotNegative();
    }
}
