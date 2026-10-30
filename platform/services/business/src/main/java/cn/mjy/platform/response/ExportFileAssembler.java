package cn.mjy.platform.response;

import java.io.FilterOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.io.UncheckedIOException;
import java.security.DigestOutputStream;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.time.Duration;
import java.util.HexFormat;
import java.util.List;
import org.springframework.stereotype.Component;

/**
 * 收尾：按序读取分片，经 {@link ExportFormat.Writer} 流式写出最终文件，同时计算大小与 SHA-256。
 * 内存占用与总行数无关——任何时刻只持有一行。
 *
 * <p>四张表：答卷、字段字典、附件清单、扩展副表作答。后三张都是长表（一条记录一行），
 * 没有数据时只剩表头，但一定在——附表在不在不该随数据变。
 */
@Component
class ExportFileAssembler {

    /** 最终文件的大小与校验和。 */
    record Written(long size, String sha256) {
    }

    private final ExportFileStore files;

    ExportFileAssembler(ExportFileStore files) {
        this.files = files;
    }

    /**
     * @param attachments 附件字节的取件口；只有附件包格式用得上，其余格式传 {@code null}
     */
    Written assemble(String key, ExportFormat format, ExportLayout layout, boolean revealSensitive,
            List<String> parts, ExportAttachments attachments, String surveyTitle, byte[] customTemplate,
            String customTemplateSha256, long maxArtifactBytes, Duration timeout) {
        List<List<String>> header = layout.responseHeader();
        ExportContent content = new ExportContent(sheets(layout, revealSensitive, parts), header.get(0),
                header.get(1), sink -> readRecords(parts, sink), attachments);
        try (ExportFileStore.Upload upload = files.create(key)) {
            MessageDigest digest = sha256();
            ExportWriteBudget budget = new ExportWriteBudget(maxArtifactBytes, timeout);
            CountingStream counted = new CountingStream(new DigestOutputStream(upload.stream(), digest), budget);
            if (customTemplate == null) {
                format.writer().write(content, counted, budget);
            } else {
                writeCustomDocxBatch(content, counted, surveyTitle, customTemplate, customTemplateSha256, budget);
            }
            counted.flush();
            upload.commit();
            return new Written(counted.count, HexFormat.of().formatHex(digest.digest()));
        } catch (IOException e) {
            throw new UncheckedIOException("could not assemble export file", e);
        }
    }

    private static void writeCustomDocxBatch(ExportContent content, OutputStream out, String surveyTitle,
            byte[] template, String expectedSha256, ExportWriteBudget budget) throws IOException {
        String actualSha256 = HexFormat.of().formatHex(sha256().digest(template));
        if (!actualSha256.equals(expectedSha256)) {
            throw new IOException("frozen Word template digest mismatch");
        }
        try (ExportZip zip = new ExportZip(out)) {
            int[] sequence = {0};
            content.records().forEach(record -> {
                budget.checkpoint();
                int index = ++sequence[0];
                String responseId = cell(record.cells(), 2);
                String version = cell(record.cells(), 0);
                String answers = answers(content, record);
                byte[] rendered = DocxTemplateRenderer.render(template,
                        new DocxTemplateRenderer.Values(surveyTitle, responseId, version, answers));
                String name = String.format(java.util.Locale.ROOT, "response-%06d-v%s-r%s.docx", index,
                        safeNumber(version), safeNumber(responseId));
                try (OutputStream entry = zip.entry(name)) {
                    entry.write(rendered);
                }
            });
        }
    }

    private static String answers(ExportContent content, ExportRecord record) {
        StringBuilder result = new StringBuilder();
        int from = Math.min(ExportLayout.FIXED_CODES.size(), record.cells().size());
        for (int i = from; i < record.cells().size(); i++) {
            String label = i < content.labels().size() ? content.labels().get(i)
                    : i < content.codes().size() ? content.codes().get(i) : "";
            if (!result.isEmpty()) {
                result.append('\n');
            }
            result.append(label).append('：').append(cell(record.cells(), i));
        }
        return result.toString();
    }

    private static String cell(List<String> cells, int index) {
        return index < cells.size() && cells.get(index) != null ? cells.get(index) : "";
    }

    private static String safeNumber(String value) {
        return value != null && value.matches("[0-9]+") ? value : "unknown";
    }

    private List<ExportSheet> sheets(ExportLayout layout, boolean revealSensitive, List<String> parts) {
        List<List<String>> header = layout.responseHeader();
        return List.of(
                new ExportSheet("responses", "答卷", header.size(), layout.variables(), sink -> {
                    for (List<String> row : header) {
                        sink.accept(row);
                    }
                    readParts(parts, ExportPartCodec.RESPONSE, sink);
                }),
                ExportSheet.of("fields", "字段字典", 1, sink -> {
                    for (List<String> row : layout.dictionaryRows(revealSensitive)) {
                        sink.accept(row);
                    }
                }),
                ExportSheet.of("attachments", "附件清单", 1, sink -> {
                    sink.accept(ExportLayout.ATTACHMENT_HEADER);
                    readParts(parts, ExportPartCodec.ATTACHMENT, sink);
                }),
                ExportSheet.of("extensions", "扩展表作答", 1, sink -> {
                    sink.accept(ExportLayout.EXTENSION_HEADER);
                    readParts(parts, ExportPartCodec.EXTENSION, sink);
                }));
    }

    private void readParts(List<String> parts, char kind, ExportSheet.RowSink sink) throws IOException {
        for (String part : parts) {
            try (InputStream in = files.open(part)) {
                ExportPartCodec.read(in, kind, sink);
            }
        }
    }

    /** 逐份答卷重放分片；与按表读出的是同一批分片，顺序也相同。 */
    private void readRecords(List<String> parts, ExportRecord.Sink sink) throws IOException {
        for (String part : parts) {
            try (InputStream in = files.open(part)) {
                ExportPartCodec.readRecords(in, sink);
            }
        }
    }

    private static MessageDigest sha256() {
        try {
            return MessageDigest.getInstance("SHA-256");
        } catch (NoSuchAlgorithmException e) {
            throw new IllegalStateException("SHA-256 is required by every JDK", e);
        }
    }

    private static final class CountingStream extends FilterOutputStream {

        private long count;
        private final ExportWriteBudget budget;

        CountingStream(OutputStream out, ExportWriteBudget budget) {
            super(out);
            this.budget = budget;
        }

        @Override
        public void write(int b) throws IOException {
            budget.beforeWrite(count, 1);
            out.write(b);
            count++;
        }

        @Override
        public void write(byte[] b, int off, int len) throws IOException {
            budget.beforeWrite(count, len);
            out.write(b, off, len);
            count += len;
        }
    }
}
