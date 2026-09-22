package cn.mjy.platform.response;

import java.io.BufferedReader;
import java.io.BufferedWriter;
import java.io.IOException;
import java.io.InputStream;
import java.io.InputStreamReader;
import java.io.OutputStream;
import java.io.OutputStreamWriter;
import java.io.Writer;
import java.nio.charset.StandardCharsets;
import java.util.List;
import tools.jackson.core.JacksonException;
import tools.jackson.core.type.TypeReference;
import tools.jackson.databind.json.JsonMapper;

/**
 * 分片的中间格式，与最终文件格式无关：每行一个前缀字母加一个 JSON 字符串数组——
 * {@code R} 是答卷表的一行，{@code A} 是附件清单的一行。JSON 转义保证一行里不会出现换行。
 * 分片里的值已经遮蔽，从不落盘未遮蔽的敏感值。
 */
final class ExportPartCodec {

    static final char RESPONSE = 'R';
    static final char ATTACHMENT = 'A';

    private static final JsonMapper JSON = JsonMapper.builder().build();
    private static final TypeReference<List<String>> CELLS = new TypeReference<>() {
    };

    private ExportPartCodec() {
    }

    static void write(ExportRowEncoder.Encoded encoded, OutputStream out) throws IOException {
        Writer writer = new BufferedWriter(new OutputStreamWriter(out, StandardCharsets.UTF_8));
        for (List<String> row : encoded.rows()) {
            line(writer, RESPONSE, row);
        }
        for (List<String> row : encoded.attachments()) {
            line(writer, ATTACHMENT, row);
        }
        writer.flush();
    }

    private static void line(Writer writer, char kind, List<String> cells) throws IOException {
        writer.write(kind);
        writer.write(JSON.writeValueAsString(cells));
        writer.write('\n');
    }

    /** 读出分片里某一类的行，逐行交给 sink；分片损坏即抛出（不会悄悄少行）。 */
    static void read(InputStream in, char kind, ExportSheet.RowSink sink) throws IOException {
        BufferedReader reader = new BufferedReader(new InputStreamReader(in, StandardCharsets.UTF_8));
        String line;
        while ((line = reader.readLine()) != null) {
            if (line.isEmpty() || (line.charAt(0) != RESPONSE && line.charAt(0) != ATTACHMENT)) {
                throw new IOException("corrupt export part");
            }
            if (line.charAt(0) != kind) {
                continue;
            }
            try {
                sink.accept(JSON.readValue(line.substring(1), CELLS));
            } catch (JacksonException e) {
                throw new IOException("corrupt export part", e);
            }
        }
    }
}
