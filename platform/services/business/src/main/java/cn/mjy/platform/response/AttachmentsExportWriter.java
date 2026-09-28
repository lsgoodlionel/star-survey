package cn.mjy.platform.response;

import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.util.List;
import java.util.Objects;

/**
 * 附件包（R06-07 的"打包"那一半，ADR 0015 增补四）：一个 ZIP，内含
 *
 * <ul>
 *   <li>{@code attachments.csv}：附件清单，与 CSV / SAV 包里的同名文件逐字节同构；</li>
 *   <li>{@code files.csv}：这一次打包的结果——每一份附件在包里的路径、取到没取到、多大、没取到是为什么；</li>
 *   <li>{@code files/<问卷号>-<答卷号>/<列名>-<序号>-<存储名>}：附件本身。</li>
 * </ul>
 *
 * <p><b>内存有界</b>：清单被重放两遍——第一遍只读每一份的落地结果（字节数取自存储的元数据，不读内容）
 * 写出索引，第二遍才逐份把字节流进 ZIP。任何时刻只持有一个传输缓冲区，与附件数、单份大小都无关。
 * 否决"先写字节再写索引"：那要把索引行攒在内存里直到最后，行数与附件数成正比。
 *
 * <p><b>不做自己的校验和</b>：引擎报的 {@code sha256}（有则有）已经在清单里。平台再算一遍要么
 * 把每一份都多读一遍，要么把摘要攒到写索引时——两条都与上一段冲突。
 */
final class AttachmentsExportWriter implements ExportFormat.Writer {

    static final String MANIFEST_SHEET = "attachments";
    static final String MANIFEST_ENTRY = "attachments.csv";
    static final String INDEX_ENTRY = "files.csv";
    static final List<String> INDEX_HEADER = List.of("version", "sid", "responseid", "column", "fieldname", "index",
            "name", "path", "status", "size", "reason");

    @Override
    public void write(ExportContent content, OutputStream out) throws IOException {
        ExportAttachments attachments = Objects.requireNonNull(content.attachments(),
                "the attachment package needs the job's attachment store");
        ExportSheet manifest = manifest(content);
        try (ExportZip zip = new ExportZip(out)) {
            try (OutputStream entry = zip.entry(MANIFEST_ENTRY)) {
                CsvExportWriter.writeSheet(manifest, entry);
            }
            try (OutputStream entry = zip.entry(INDEX_ENTRY)) {
                CsvExportWriter.writeSheet(index(manifest, attachments), entry);
            }
            writeFiles(zip, manifest, attachments);
        }
    }

    private static ExportSheet manifest(ExportContent content) {
        return content.sheets().stream().filter(sheet -> MANIFEST_SHEET.equals(sheet.fileName())).findFirst()
                .orElseThrow(() -> new IllegalStateException("the export content has no attachment manifest"));
    }

    /** 索引表：清单的每一行加上它的落地结果。 */
    private static ExportSheet index(ExportSheet manifest, ExportAttachments attachments) {
        return ExportSheet.of("files", "附件文件", 1, sink -> {
            sink.accept(INDEX_HEADER);
            eachFile(manifest, (row, ref) -> {
                ExportAttachments.Outcome outcome = attachments.outcome(ref);
                sink.accept(List.of(cell(row, "version"), cell(row, "sid"), cell(row, "responseid"),
                        cell(row, "column"), cell(row, "fieldname"), cell(row, "index"), cell(row, "name"),
                        ref.pathInZip(), outcome.status(), outcome.stored() ? Long.toString(outcome.size()) : "",
                        outcome.reason()));
            });
        });
    }

    private static void writeFiles(ExportZip zip, ExportSheet manifest, ExportAttachments attachments)
            throws IOException {
        eachFile(manifest, (row, ref) -> {
            if (!attachments.outcome(ref).stored()) {
                return;
            }
            try (InputStream bytes = attachments.open(ref); OutputStream entry = zip.entry(ref.pathInZip())) {
                bytes.transferTo(entry);
            }
        });
    }

    /** 重放清单的数据行（跳过表头），逐行交给 visitor。 */
    private static void eachFile(ExportSheet manifest, FileVisitor visitor) throws IOException {
        int[] seen = {0};
        manifest.rows().forEach(row -> {
            if (seen[0]++ < manifest.headerRows()) {
                return;
            }
            visitor.accept(row, ExportAttachmentRef.of(row));
        });
    }

    private static String cell(List<String> row, String column) {
        return row.get(ExportLayout.ATTACHMENT_HEADER.indexOf(column));
    }

    @FunctionalInterface
    private interface FileVisitor {
        void accept(List<String> row, ExportAttachmentRef ref) throws IOException;
    }
}
