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
        List<ExportSheet> sheets = List.of(
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
                }));
        try (ExportFileStore.Upload upload = files.create(key)) {
            MessageDigest digest = sha256();
            CountingStream counted = new CountingStream(new DigestOutputStream(upload.stream(), digest));
            format.writer().write(sheets, counted);
            counted.flush();
            upload.commit();
            return new Written(counted.count, HexFormat.of().formatHex(digest.digest()));
        } catch (IOException e) {
            throw new UncheckedIOException("could not assemble export file", e);
        }
    }

    private void readParts(List<String> parts, char kind, ExportSheet.RowSink sink) throws IOException {
        for (String part : parts) {
            try (InputStream in = files.open(part)) {
                ExportPartCodec.read(in, kind, sink);
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
