package cn.mjy.platform.response;

import static org.assertj.core.api.Assertions.assertThat;

import java.io.ByteArrayInputStream;
import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.nio.charset.StandardCharsets;
import java.nio.file.Path;
import cn.mjy.platform.shared.storage.LocalBlobStore;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.concurrent.atomic.AtomicInteger;
import java.util.zip.ZipEntry;
import java.util.zip.ZipInputStream;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

/**
 * 纯单元：附件包的构成、索引表、缺失项的记法、包内路径不撞、逐字节可复现，
 * 以及"索引不读文件内容"（字节数取自存储元数据）。
 */
class AttachmentsExportFormatTest {

    private static final String PREFIX = "t1/00000000-0000-4000-8000-000000000001";

    @TempDir
    private Path root;

    /** 附件清单的三行：同一份答卷的两道上传题（序号都从 1 起），外加另一份答卷的一份。 */
    private static List<List<String>> manifestRows() {
        return List.of(ExportLayout.ATTACHMENT_HEADER,
                row("1", "900", "7", "QFILE", "F900", "1", "=cmd|'/C calc'!A0.png", "3.2", "png", "fu_a", ""),
                row("1", "900", "7", "QSCAN", "F901", "1", "b.pdf", "10", "pdf", "fu_b", ""),
                row("2", "901", "7", "QFILE", "F900", "1", "c.txt", "1", "txt", "fu_c", ""));
    }

    private static List<String> row(String... cells) {
        return Arrays.asList(cells);
    }

    private static ExportSheet manifest() {
        return ExportSheet.of(AttachmentsExportWriter.MANIFEST_SHEET, "附件清单", 1, sink -> {
            for (List<String> row : manifestRows()) {
                sink.accept(row);
            }
        });
    }

    private static ExportAttachmentRef ref(int index) {
        return ExportAttachmentRef.of(manifestRows().get(index));
    }

    private CountingStore store() {
        return new CountingStore(root);
    }

    private static byte[] write(CountingStore files) throws IOException {
        ExportAttachments attachments = new ExportAttachments(files, PREFIX);
        ExportContent content = new ExportContent(List.of(manifest()), List.of(), List.of(), sink -> {
        }, attachments);
        ByteArrayOutputStream out = new ByteArrayOutputStream();
        ExportFormat.ATTACHMENTS.writer().write(content, out);
        return out.toByteArray();
    }

    private static void store(CountingStore files, ExportAttachmentRef ref, String content) throws IOException {
        try (ExportFileStore.Upload upload = files.create(new ExportAttachments(files, PREFIX).key(ref))) {
            upload.stream().write(content.getBytes(StandardCharsets.UTF_8));
            upload.commit();
        }
    }

    private static Map<String, byte[]> unzip(byte[] zip) throws IOException {
        Map<String, byte[]> entries = new LinkedHashMap<>();
        try (ZipInputStream in = new ZipInputStream(new ByteArrayInputStream(zip))) {
            ZipEntry entry;
            while ((entry = in.getNextEntry()) != null) {
                entries.put(entry.getName(), in.readAllBytes());
            }
        }
        return entries;
    }

    private static List<List<String>> csv(byte[] bytes) {
        return ExportFixture.parseCsv(new String(bytes, 3, bytes.length - 3, StandardCharsets.UTF_8));
    }

    @Test
    void thePackageHoldsTheManifestTheIndexAndOneEntryPerStoredFile() throws IOException {
        CountingStore files = store();
        store(files, ref(1), "first");
        store(files, ref(2), "second");
        store(files, ref(3), "third");

        Map<String, byte[]> entries = unzip(write(files));

        assertThat(entries).containsOnlyKeys(AttachmentsExportWriter.MANIFEST_ENTRY,
                AttachmentsExportWriter.INDEX_ENTRY,
                "files/900-7/F900-1-fu_a", "files/900-7/F901-1-fu_b", "files/901-7/F900-1-fu_c");
        assertThat(entries.get("files/900-7/F901-1-fu_b")).asString(StandardCharsets.UTF_8).isEqualTo("second");
        assertThat(csv(entries.get(AttachmentsExportWriter.MANIFEST_ENTRY)))
                .containsExactlyElementsOf(guarded(manifestRows()));
    }

    /** 清单里的单元格照样走防公式注入：包里的这张表同样会被粘进 Excel。 */
    private static List<List<String>> guarded(List<List<String>> rows) {
        List<List<String>> expected = new ArrayList<>();
        rows.forEach(row -> expected.add(row.stream().map(ExportCellGuard::guard).toList()));
        return expected;
    }

    @Test
    void theIndexNamesEveryFileWithItsPathStatusAndSize() throws IOException {
        CountingStore files = store();
        store(files, ref(1), "first");
        store(files, ref(3), "third");
        new ExportAttachments(files, PREFIX).markAbsent(ref(2), ExportAttachments.REASON_NOT_FOUND);

        List<List<String>> index = csv(unzip(write(files)).get(AttachmentsExportWriter.INDEX_ENTRY));

        assertThat(index.get(0)).containsExactlyElementsOf(AttachmentsExportWriter.INDEX_HEADER);
        assertThat(index).hasSize(4);
        assertThat(index.get(1)).containsSequence("1", "900", "7", "QFILE", "F900", "1")
                .containsSequence("files/900-7/F900-1-fu_a", "stored", "5", "");
        assertThat(index.get(2)).containsSequence("files/900-7/F901-1-fu_b", "absent", "", "not_found");
    }

    /** 没有结论的那一份如实记 unknown：分批阶段本该给每一份留下结论，不假装它从来不存在。 */
    @Test
    void anAttachmentWithNoOutcomeAtAllIsReportedAsUnknownRatherThanSkipped() throws IOException {
        CountingStore files = store();

        List<List<String>> index = csv(unzip(write(files)).get(AttachmentsExportWriter.INDEX_ENTRY));

        assertThat(index).hasSize(4);
        assertThat(index.subList(1, 4)).allSatisfy(row ->
                assertThat(row).containsSequence("absent", "", ExportAttachments.REASON_UNKNOWN));
    }

    /** 索引只看元数据：内容被打开的次数恰好等于真正进包的份数。 */
    @Test
    void writingTheIndexDoesNotReadTheFileContents() throws IOException {
        CountingStore files = store();
        store(files, ref(1), "first");
        store(files, ref(2), "second");

        write(files);

        assertThat(files.opened.get()).isEqualTo(2);
    }

    @Test
    void theSameInputProducesAByteIdenticalPackage() throws IOException {
        CountingStore files = store();
        store(files, ref(1), "first");
        new ExportAttachments(files, PREFIX).markAbsent(ref(2), ExportAttachments.REASON_TOO_LARGE);
        store(files, ref(3), "third");

        assertThat(write(files)).isEqualTo(write(files));
    }

    /** 存储名里的奇怪字符不进包内路径；空存储名退回固定名，不产生目录穿越。 */
    @Test
    void theInZipPathIsSanitisedAndNeverEscapesTheFilesFolder() {
        assertThat(new ExportAttachmentRef(1, 9, 7, "F900", 2, "../../etc/passwd").pathInZip())
                .isEqualTo("files/9-7/F900-2-.._.._etc_passwd");
        assertThat(new ExportAttachmentRef(1, 9, 7, "F900", 2, "").pathInZip()).isEqualTo("files/9-7/F900-2-file");
        assertThat(new ExportAttachmentRef(1, 9, 7, "F900", 2, "..").pathInZip()).isEqualTo("files/9-7/F900-2-file");
    }

    /** 统计"内容被打开了几次"的本地存储，其余行为与生产实现相同。 */
    private static final class CountingStore extends LocalBlobStore implements ExportFileStore {

        private final AtomicInteger opened = new AtomicInteger();

        private CountingStore(Path root) {
            super(root);
        }

        @Override
        public InputStream open(String key) throws IOException {
            if (!key.endsWith(".gone")) {
                opened.incrementAndGet();
            }
            return super.open(key);
        }
    }
}
