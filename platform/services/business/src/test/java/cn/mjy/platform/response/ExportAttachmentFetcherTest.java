package cn.mjy.platform.response;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import cn.mjy.platform.response.ExportPlan.PlannedSource;
import cn.mjy.platform.response.ResponseAttachmentSource.Outcome;
import cn.mjy.platform.shared.TenantId;
import cn.mjy.platform.shared.storage.LocalBlobStore;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.io.UncheckedIOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Path;
import java.time.Duration;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.UUID;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

/**
 * 取件阶段：一次一份流式落盘、已有结论的跳过、引擎里没有的留墓碑、暂时失败抛出且不留墓碑。
 * 这四条就是「内存有界」与「失败项可重试」在平台侧的全部机制（R06-07）。
 */
class ExportAttachmentFetcherTest {

    private static final TenantId TENANT = TenantId.of("11111111-0000-4000-8000-000000000001");
    private static final UUID JOB = UUID.fromString("00000000-0000-4000-8000-00000000000a");
    private static final String INSTANCE = "engine-a";
    private static final String GENERATION = "g1";
    private static final long SID = 900;

    @TempDir
    private Path root;

    private final FakeAttachmentSource source = new FakeAttachmentSource();

    private ExportFileStore files;

    private ExportFileStore files() {
        if (files == null) {
            files = new TestStore(root);
        }
        return files;
    }

    private ExportAttachmentFetcher fetcher() {
        return new ExportAttachmentFetcher(files(), source, properties());
    }

    /** 默认配置，只把单份上限压到很小，好让"超限"与"正常"在同一份配置下都跑得出来。 */
    private static ResponseExportProperties properties() {
        return new ResponseExportProperties(false, Duration.ofSeconds(10), Duration.ZERO, "", Duration.ofHours(72),
                500, 5000, Duration.ofMinutes(5), 5, Duration.ofMinutes(1), 200, 1024);
    }

    private static ExportPlan plan() {
        return new ExportPlan(List.of(
                new PlannedSource(1, INSTANCE, SID, GENERATION, List.of(), List.of())));
    }

    /** 一份答卷、两道上传题各一份附件。 */
    private static ExportRowEncoder.Encoded batch(String... storedNames) {
        List<List<String>> rows = new ArrayList<>();
        for (int i = 0; i < storedNames.length; i++) {
            rows.add(Arrays.asList("1", Long.toString(SID), "7", "QFILE" + i, "F90" + i, "1",
                    "原名" + i + ".png", "3", "png", storedNames[i], ""));
        }
        return new ExportRowEncoder.Encoded(
                List.of(new ExportRecord(List.of("1"), List.copyOf(rows), List.of())));
    }

    private static ExportAttachmentRef ref(String fieldname, String storedName) {
        return new ExportAttachmentRef(1, SID, 7, fieldname, 1, storedName);
    }

    private String stored(ExportAttachmentRef ref) {
        ExportAttachments attachments = new ExportAttachments(files(), ResponseExportWorker.jobPrefix(TENANT, JOB));
        try (InputStream in = attachments.open(ref)) {
            return new String(in.readAllBytes(), StandardCharsets.UTF_8);
        } catch (IOException e) {
            throw new UncheckedIOException(e);
        }
    }

    private ExportAttachments attachments() {
        return new ExportAttachments(files(), ResponseExportWorker.jobPrefix(TENANT, JOB));
    }

    @Test
    void everyAttachmentOfTheBatchLandsUnderItsOwnKey() throws IOException {
        source.put("fu_a", "alpha");
        source.put("fu_b", "beta");

        int fetched = fetcher().fetch(TENANT, JOB, plan(), batch("fu_a", "fu_b"));

        assertThat(fetched).isEqualTo(2);
        assertThat(stored(ref("F900", "fu_a"))).isEqualTo("alpha");
        assertThat(stored(ref("F901", "fu_b"))).isEqualTo("beta");
        assertThat(attachments().outcome(ref("F900", "fu_a")).size()).isEqualTo(5);
    }

    /** 重跑同一批（崩溃恢复、暂时失败后重试）不再去问引擎：已经有结论的那些直接跳过。 */
    @Test
    void anAttachmentThatAlreadyHasAnOutcomeIsNotFetchedAgain() {
        source.put("fu_a", "alpha");
        fetcher().fetch(TENANT, JOB, plan(), batch("fu_a", "fu_b"));
        source.calls.clear();

        int fetched = fetcher().fetch(TENANT, JOB, plan(), batch("fu_a", "fu_b"));

        assertThat(fetched).isZero();
        assertThat(source.calls).isEmpty();
    }

    /** 引擎里已经没有的那一份留墓碑、不中断同一批里其余的附件——一个附件失败不让整包重来。 */
    @Test
    void anAttachmentTheEngineNoLongerHasIsRecordedAndTheRestOfTheBatchContinues() throws IOException {
        source.put("fu_b", "beta");

        int fetched = fetcher().fetch(TENANT, JOB, plan(), batch("fu_a", "fu_b"));

        assertThat(fetched).isEqualTo(1);
        ExportAttachments.Outcome absent = attachments().outcome(ref("F900", "fu_a"));
        assertThat(absent.stored()).isFalse();
        assertThat(absent.reason()).isEqualTo(ExportAttachments.REASON_NOT_FOUND);
        assertThat(stored(ref("F901", "fu_b"))).isEqualTo("beta");
    }

    @Test
    void anAttachmentOverTheSizeLimitIsRecordedAsTooLargeWithoutHalfAFile() throws IOException {
        source.tooLarge("fu_a");

        fetcher().fetch(TENANT, JOB, plan(), batch("fu_a"));

        ExportAttachments.Outcome outcome = attachments().outcome(ref("F900", "fu_a"));
        assertThat(outcome.stored()).isFalse();
        assertThat(outcome.reason()).isEqualTo(ExportAttachments.REASON_TOO_LARGE);
    }

    /** 暂时失败抛出，且**不留墓碑**：下一轮必须重新去取，不能悄悄把它记成缺失。 */
    @Test
    void aTransientFailureIsRaisedAndLeavesNoOutcomeBehind() {
        source.put("fu_a", "alpha");
        source.failFor("fu_b");

        assertThatThrownBy(() -> fetcher().fetch(TENANT, JOB, plan(), batch("fu_a", "fu_b")))
                .isInstanceOf(ResponseAttachmentsUnavailableException.class);

        assertThat(attachments().resolved(ref("F900", "fu_a"))).isTrue();
        assertThat(attachments().resolved(ref("F901", "fu_b"))).isFalse();
    }

    /** 半路失败的那一份不留半截文件：流没提交，存储里什么都没有。 */
    @Test
    void aFailureHalfwayThroughOneFileLeavesNothingBehind() {
        source.failAfterWriting("fu_a", "half");

        assertThatThrownBy(() -> fetcher().fetch(TENANT, JOB, plan(), batch("fu_a")))
                .isInstanceOf(ResponseAttachmentsUnavailableException.class);

        assertThat(attachments().resolved(ref("F900", "fu_a"))).isFalse();
    }

    /** 请求里带的是代次与引擎列名，绝不带作答者起的原始文件名。 */
    @Test
    void theRequestNamesTheGenerationAndColumnButNeverTheOriginalFileName() {
        source.put("fu_a", "alpha");

        fetcher().fetch(TENANT, JOB, plan(), batch("fu_a"));

        AttachmentQuery query = source.calls.get(0);
        assertThat(query.generation()).isEqualTo(GENERATION);
        assertThat(query.engineInstanceId()).isEqualTo(INSTANCE);
        assertThat(query.engineSid()).isEqualTo(SID);
        assertThat(query.responseId()).isEqualTo(7);
        assertThat(query.fieldname()).isEqualTo("F900");
        assertThat(query.storedName()).isEqualTo("fu_a");
        assertThat(query.maxBytes()).isPositive();
    }

    private static final class TestStore extends LocalBlobStore implements ExportFileStore {

        private TestStore(Path root) {
            super(root);
        }
    }

    /** 按存储名给内容的替身；也能演暂时失败、半路失败与超限。 */
    private static final class FakeAttachmentSource implements ResponseAttachmentSource {

        private final Map<String, String> contents = new LinkedHashMap<>();
        private final Map<String, String> halfway = new LinkedHashMap<>();
        private final List<String> failing = new ArrayList<>();
        private final List<String> tooLarge = new ArrayList<>();
        private final List<AttachmentQuery> calls = new ArrayList<>();

        void put(String storedName, String content) {
            contents.put(storedName, content);
        }

        void failFor(String storedName) {
            failing.add(storedName);
        }

        void tooLarge(String storedName) {
            tooLarge.add(storedName);
        }

        void failAfterWriting(String storedName, String partial) {
            halfway.put(storedName, partial);
            failing.add(storedName);
        }

        @Override
        public Outcome fetch(AttachmentQuery query, OutputStream sink) {
            calls.add(query);
            String name = query.storedName();
            if (halfway.containsKey(name)) {
                write(sink, halfway.get(name));
            }
            if (failing.contains(name)) {
                throw new ResponseAttachmentsUnavailableException("fake gateway failure");
            }
            if (tooLarge.contains(name)) {
                return Outcome.TOO_LARGE;
            }
            String content = contents.get(name);
            if (content == null) {
                return Outcome.NOT_FOUND;
            }
            write(sink, content);
            return Outcome.STORED;
        }

        private static void write(OutputStream sink, String content) {
            try {
                sink.write(content.getBytes(StandardCharsets.UTF_8));
            } catch (IOException e) {
                throw new UncheckedIOException(e);
            }
        }
    }
}
