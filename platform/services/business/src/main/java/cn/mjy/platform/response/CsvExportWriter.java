package cn.mjy.platform.response;

import java.io.BufferedWriter;
import java.io.IOException;
import java.io.OutputStream;
import java.io.OutputStreamWriter;
import java.io.Writer;
import java.nio.charset.StandardCharsets;
import java.util.List;
import java.util.regex.Pattern;

/**
 * CSV 包：每张表一个 UTF-8 带 BOM 的 CSV 文件（Excel 直接双击打开不乱码），CRLF 行尾，RFC 4180 引号规则。
 *
 * <p>防公式注入（R06-02）：以 {@code = + - @}、制表符或回车开头、且不是纯数字的单元格前加一个单引号，
 * Excel / WPS 打开时按文本显示而不执行。纯数字（含负数、小数）保持原样。
 */
final class CsvExportWriter implements ExportFormat.Writer {

    private static final byte[] BOM = {(byte) 0xEF, (byte) 0xBB, (byte) 0xBF};
    private static final String LINE_END = "\r\n";
    private static final Pattern NUMBER = Pattern.compile("[+-]?(\\d+(\\.\\d*)?|\\.\\d+)([eE][+-]?\\d+)?");
    private static final String FORMULA_PREFIXES = "=+-@\t\r";

    @Override
    public void write(List<ExportSheet> sheets, OutputStream out) throws IOException {
        try (ExportZip zip = new ExportZip(out)) {
            for (ExportSheet sheet : sheets) {
                try (OutputStream entry = zip.entry(sheet.fileName() + ".csv")) {
                    entry.write(BOM);
                    Writer writer = new BufferedWriter(new OutputStreamWriter(entry, StandardCharsets.UTF_8));
                    sheet.rows().forEach(cells -> writeRow(writer, cells));
                    writer.flush();
                }
            }
        }
    }

    private static void writeRow(Writer writer, List<String> cells) throws IOException {
        for (int i = 0; i < cells.size(); i++) {
            if (i > 0) {
                writer.write(',');
            }
            writer.write(quote(guard(cells.get(i))));
        }
        writer.write(LINE_END);
    }

    static String guard(String value) {
        if (value == null || value.isEmpty()) {
            return "";
        }
        if (FORMULA_PREFIXES.indexOf(value.charAt(0)) >= 0 && !NUMBER.matcher(value).matches()) {
            return "'" + value;
        }
        return value;
    }

    private static String quote(String value) {
        boolean needsQuotes = value.indexOf(',') >= 0 || value.indexOf('"') >= 0
                || value.indexOf('\n') >= 0 || value.indexOf('\r') >= 0;
        return needsQuotes ? '"' + value.replace("\"", "\"\"") + '"' : value;
    }
}
