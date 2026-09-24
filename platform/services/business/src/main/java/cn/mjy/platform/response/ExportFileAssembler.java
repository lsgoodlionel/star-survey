package cn.mjy.platform.response;

import java.io.FilterOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.io.UncheckedIOException;
import java.security.DigestOutputStream;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
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

    Written assemble(String key, ExportFormat format, ExportLayout layout, boolean revealSensitive,
            List<String> parts) {
        List<List<String>> header = layout.responseHeader();
        ExportContent content = new ExportContent(sheets(layout, revealSensitive, parts), header.get(0),
                header.get(1), sink -> readRecords(parts, sink));
        try (ExportFileStore.Upload upload = files.create(key)) {
            MessageDigest digest = sha256();
            CountingStream counted = new CountingStream(new DigestOutputStream(upload.stream(), digest));
            format.writer().write(content, counted);
            counted.flush();
            upload.commit();
            return new Written(counted.count, HexFormat.of().formatHex(digest.digest()));
        } catch (IOException e) {
            throw new UncheckedIOException("could not assemble export file", e);
        }
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

        CountingStream(OutputStream out) {
            super(out);
        }

        @Override
        public void write(int b) throws IOException {
            out.write(b);
            count++;
        }

        @Override
        public void write(byte[] b, int off, int len) throws IOException {
            out.write(b, off, len);
            count += len;
        }
    }
}
