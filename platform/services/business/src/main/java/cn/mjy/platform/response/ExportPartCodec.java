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
import java.util.ArrayList;
import java.util.List;
import tools.jackson.core.JacksonException;
import tools.jackson.core.type.TypeReference;
import tools.jackson.databind.json.JsonMapper;

/**
 * 分片的中间格式，与最终文件格式无关：每行一个前缀字母加一个 JSON 字符串数组——
 * {@code R} 是答卷表的一行，{@code A} 是它的一个附件，{@code E} 是它的一个副表单元格。
 * JSON 转义保证一行里不会出现换行。分片里的值已经遮蔽，从不落盘未遮蔽的敏感值。
 *
 * <p>三类行<b>按答卷交替存放</b>：一条 {@code R} 之后紧跟属于它的 {@code A} 与 {@code E}，
 * 下一条 {@code R} 开始下一份答卷。于是两种读法都是流式的，任何时刻只持有一行或一份答卷：
 * {@link #read} 按类过滤出一张表，{@link #readRecords} 逐份答卷读出。
 */
final class ExportPartCodec {

    static final char RESPONSE = 'R';
    static final char ATTACHMENT = 'A';
    static final char EXTENSION = 'E';

    private static final String KINDS = "" + RESPONSE + ATTACHMENT + EXTENSION;
    private static final JsonMapper JSON = JsonMapper.builder().build();
    private static final TypeReference<List<String>> CELLS = new TypeReference<>() {
    };

    private ExportPartCodec() {
    }

    static void write(ExportRowEncoder.Encoded encoded, OutputStream out) throws IOException {
        Writer writer = new BufferedWriter(new OutputStreamWriter(out, StandardCharsets.UTF_8));
        for (ExportRecord record : encoded.records()) {
            line(writer, RESPONSE, record.cells());
            for (List<String> row : record.attachments()) {
                line(writer, ATTACHMENT, row);
            }
            for (List<String> row : record.extensions()) {
                line(writer, EXTENSION, row);
            }
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
            if (kindOf(line) == kind) {
                sink.accept(cells(line));
            }
        }
    }

    /** 逐份答卷读出分片；一条 {@code R} 与它后面的 {@code A} / {@code E} 是一份。 */
    static void readRecords(InputStream in, ExportRecord.Sink sink) throws IOException {
        BufferedReader reader = new BufferedReader(new InputStreamReader(in, StandardCharsets.UTF_8));
        List<String> cells = null;
        List<List<String>> attachments = new ArrayList<>();
        List<List<String>> extensions = new ArrayList<>();
        String line;
        while ((line = reader.readLine()) != null) {
            char kind = kindOf(line);
            if (kind == RESPONSE) {
                if (cells != null) {
                    sink.accept(new ExportRecord(cells, attachments, extensions));
                }
                cells = cells(line);
                attachments = new ArrayList<>();
                extensions = new ArrayList<>();
            } else if (cells == null) {
                throw new IOException("corrupt export part");
            } else if (kind == ATTACHMENT) {
                attachments.add(cells(line));
            } else {
                extensions.add(cells(line));
            }
        }
        if (cells != null) {
            sink.accept(new ExportRecord(cells, attachments, extensions));
        }
    }

    /** 行首的类别字母；空行或不认识的字母都算分片损坏。 */
    private static char kindOf(String line) throws IOException {
        if (line.isEmpty() || KINDS.indexOf(line.charAt(0)) < 0) {
            throw new IOException("corrupt export part");
        }
        return line.charAt(0);
    }

    /** 一行的单元格；JSON 坏了算分片损坏。 */
    private static List<String> cells(String line) throws IOException {
        try {
            return JSON.readValue(line.substring(1), CELLS);
        } catch (JacksonException e) {
            throw new IOException("corrupt export part", e);
        }
    }
}
