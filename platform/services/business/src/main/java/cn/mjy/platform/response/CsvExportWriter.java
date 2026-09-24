package cn.mjy.platform.response;

import java.io.BufferedWriter;
import java.io.IOException;
import java.io.OutputStream;
import java.io.OutputStreamWriter;
import java.io.Writer;
import java.nio.charset.StandardCharsets;
import java.util.List;

/**
 * CSV 包：每张表一个 UTF-8 带 BOM 的 CSV 文件（Excel 直接双击打开不乱码），CRLF 行尾，RFC 4180 引号规则。
 * 防公式注入见 {@link ExportCellGuard}。
 */
final class CsvExportWriter implements ExportFormat.Writer {

    private static final byte[] BOM = {(byte) 0xEF, (byte) 0xBB, (byte) 0xBF};
    private static final String LINE_END = "\r\n";

    @Override
    public void write(ExportContent content, OutputStream out) throws IOException {
        try (ExportZip zip = new ExportZip(out)) {
            for (ExportSheet sheet : content.sheets()) {
                try (OutputStream entry = zip.entry(sheet.fileName() + ".csv")) {
                    writeSheet(sheet, entry);
                }
            }
        }
    }

    /** 把一张表写成一个带 BOM 的 CSV 流；SAV 包里的附表也走这里（同样的转义与防护）。 */
    static void writeSheet(ExportSheet sheet, OutputStream out) throws IOException {
        out.write(BOM);
        Writer writer = new BufferedWriter(new OutputStreamWriter(out, StandardCharsets.UTF_8));
        sheet.rows().forEach(cells -> writeRow(writer, cells));
        writer.flush();
    }

    private static void writeRow(Writer writer, List<String> cells) throws IOException {
        for (int i = 0; i < cells.size(); i++) {
            if (i > 0) {
                writer.write(',');
            }
            writer.write(quote(ExportCellGuard.guard(cells.get(i))));
        }
        writer.write(LINE_END);
    }

    private static String quote(String value) {
        boolean needsQuotes = value.indexOf(',') >= 0 || value.indexOf('"') >= 0
                || value.indexOf('\n') >= 0 || value.indexOf('\r') >= 0;
        return needsQuotes ? '"' + value.replace("\"", "\"\"") + '"' : value;
    }
}
