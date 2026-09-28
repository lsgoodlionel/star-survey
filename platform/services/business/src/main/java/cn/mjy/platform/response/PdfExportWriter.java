package cn.mjy.platform.response;

import java.io.IOException;
import java.io.OutputStream;
import java.util.ArrayList;
import java.util.List;
import java.util.Locale;

/**
 * 只用 JDK 写出的 PDF 1.7：逐份答卷成文（R06-05，ADR 0015 增补六）。每份答卷起一页，
 * 页内依次是「答卷信息」「作答」「结构化作答」「附件」——排版结构与 {@link DocxExportWriter}
 * 共用同一套 {@link ExportRecord} 视图与同一份副表透视（{@link ExtensionTables#pivot}），
 * 差别只在写出器。
 *
 * <p><b>字体随交付自带并嵌入</b>，不依赖阅读器（冻结工作表 P-10 否决了靠阅读器 CJK 字体包
 * 那条备选）。{@link PdfFontProgram} 读出自带的 GB2312 子集，{@link PdfFontUse} 记下这一份
 * 文档用到哪些字，收尾时只把那些字形嵌进 {@code /FontFile2}。子集外的字符画成替代符，
 * 但 {@code ToUnicode} 里仍是原文——理由见 {@link PdfFontUse}。
 *
 * <p>防公式注入照旧走 {@link ExportCellGuard}（在 {@link PdfPageWriter} 的表格出口上）：
 * PDF 自己不算公式，但整页可以复制进 Excel，那一步又回到会执行公式的地方——与 SAV、DOCX
 * 同一条理由，不按格式开口子。
 *
 * <p><b>内存有界</b>：对答卷只扫一遍，任何时刻只持有一份答卷与当前一页的内容流。
 * 页对象先写、字体后写（PDF 允许前向引用），所以不必把页缓存起来等字体定下来。
 */
final class PdfExportWriter implements ExportFormat.Writer {

    /**
     * 一份文档里最多放多少份答卷。与 {@link DocxExportWriter#MAX_RESPONSES} 同一个数、
     * 同一个处置：超出时作业失败（{@code too_many_rows}），而不是产出一份没人打得开的文件。
     */
    static final long MAX_RESPONSES = 10_000L;

    /** {@link ExportLayout#FIXED_CODES} 里 {@code responseid} 的下标。 */
    private static final int RESPONSE_ID = 2;

    /** 附件表的表头与取列范围，与 DOCX 一致。 */
    private static final List<String> ATTACHMENT_COLUMNS = List.of("序号", "名称", "大小", "类型", "存储名", "sha256");
    private static final int ATTACHMENT_FROM = 5;

    /** 一份答卷都没有时也要出一页，写这句话——一份零页的 PDF 打不开。 */
    private static final String NO_RESPONSES = "这次导出没有任何答卷。";

    @Override
    public long maxDataRows() {
        return MAX_RESPONSES;
    }

    @Override
    public void write(ExportContent content, OutputStream out) throws IOException {
        PdfFontUse font = new PdfFontUse(PdfFontProgram.get());
        PdfObjects objects = new PdfObjects(out);
        objects.header();

        int catalog = objects.reserve();
        int tree = objects.reserve();
        int type0 = objects.reserve();
        int cidFont = objects.reserve();
        int descriptor = objects.reserve();
        int program = objects.reserve();
        int cidToGid = objects.reserve();
        int toUnicode = objects.reserve();

        List<Integer> pages = new ArrayList<>();
        PdfPageWriter writer = new PdfPageWriter(font, page -> {
            int number = objects.reserve();
            int stream = objects.reserve();
            objects.dictionary(number, page(number, tree, type0, stream));
            objects.stream(stream, "", page);
            pages.add(number);
        });

        int[] written = {0};
        content.records().forEach(record -> {
            writer.newPage();
            section(writer, content, record);
            written[0]++;
        });
        if (written[0] == 0) {
            writer.paragraph(NO_RESPONSES);
        }
        writer.finish();

        objects.dictionary(catalog, "<< /Type /Catalog /Pages " + tree + " 0 R >>");
        objects.dictionary(tree, pageTree(pages));
        objects.dictionary(type0, type0Font(font, cidFont, toUnicode));
        objects.dictionary(cidFont, cidFont(font, descriptor, cidToGid));
        objects.dictionary(descriptor, descriptor(font, program));
        byte[] subset = font.program().subset(font.usedGlyphs());
        objects.stream(program, " /Length1 " + subset.length, subset);
        objects.stream(cidToGid, "", font.cidToGidMap());
        objects.stream(toUnicode, "", font.toUnicodeCmap());
        objects.trailer(catalog);
    }

    // ---- 一份答卷的版面 ----

    private static void section(PdfPageWriter writer, ExportContent content, ExportRecord record)
            throws IOException {
        List<String> cells = record.cells();
        writer.heading(1, "答卷 " + cell(cells, RESPONSE_ID));
        int fixed = Math.min(ExportLayout.FIXED_CODES.size(), cells.size());
        writer.heading(2, "答卷信息");
        writer.table(labelValues(content, record, 0, fixed));
        writer.heading(2, "作答");
        writer.table(labelValues(content, record, fixed, cells.size()));
        for (ExtensionTables.Pivoted question : ExtensionTables.pivot(content, record)) {
            writer.heading(2, question.title());
            if (question.note().isEmpty()) {
                writer.table(question.rows());
            } else {
                writer.paragraph(question.note());
            }
        }
        attachments(writer, record);
    }

    /** 「标签｜值」两列表；标签取答卷表表头第二行，值取这一份答卷的那一格。 */
    private static List<List<String>> labelValues(ExportContent content, ExportRecord record, int from, int to) {
        List<List<String>> rows = new ArrayList<>(Math.max(0, to - from));
        for (int i = from; i < to; i++) {
            String label = i < content.labels().size() ? content.labels().get(i)
                    : i < content.codes().size() ? content.codes().get(i) : "";
            rows.add(List.of(label, cell(record.cells(), i)));
        }
        return rows;
    }

    private static void attachments(PdfPageWriter writer, ExportRecord record) throws IOException {
        if (record.attachments().isEmpty()) {
            return;
        }
        writer.heading(2, "附件");
        List<List<String>> rows = new ArrayList<>(record.attachments().size() + 1);
        rows.add(ATTACHMENT_COLUMNS);
        for (List<String> attachment : record.attachments()) {
            rows.add(List.copyOf(attachment.subList(ATTACHMENT_FROM,
                    Math.min(ATTACHMENT_FROM + ATTACHMENT_COLUMNS.size(), attachment.size()))));
        }
        writer.table(rows);
        writer.paragraph("");
    }

    private static String cell(List<String> cells, int index) {
        String value = index < cells.size() ? cells.get(index) : null;
        return value == null ? "" : value;
    }

    // ---- 对象 ----

    private static String page(int number, int tree, int font, int contents) {
        return "<< /Type /Page /Parent " + tree + " 0 R /MediaBox [0 0 "
                + String.format(Locale.ROOT, "%.2f %.2f", PdfPageWriter.PAGE_WIDTH, PdfPageWriter.PAGE_HEIGHT)
                + "] /Resources << /Font << /F1 " + font + " 0 R >> >> /Contents " + contents + " 0 R >>";
    }

    private static String pageTree(List<Integer> pages) {
        StringBuilder kids = new StringBuilder("[");
        pages.forEach(page -> kids.append(page).append(" 0 R "));
        return "<< /Type /Pages /Count " + pages.size() + " /Kids " + kids.append("]") + " >>";
    }

    private static String type0Font(PdfFontUse font, int cidFont, int toUnicode) {
        return "<< /Type /Font /Subtype /Type0 /BaseFont /" + font.baseFont()
                + " /Encoding /Identity-H /DescendantFonts [" + cidFont + " 0 R] /ToUnicode "
                + toUnicode + " 0 R >>";
    }

    private static String cidFont(PdfFontUse font, int descriptor, int cidToGid) {
        return "<< /Type /Font /Subtype /CIDFontType2 /BaseFont /" + font.baseFont()
                + " /CIDSystemInfo << /Registry (Adobe) /Ordering (Identity) /Supplement 0 >>"
                + " /FontDescriptor " + descriptor + " 0 R /DW 1000 /W " + font.widths()
                + " /CIDToGIDMap " + cidToGid + " 0 R >>";
    }

    /**
     * 字体描述符。{@code /Flags} 的第 3 位（值 4）是 Symbolic：复合字体的编码由 CMap 决定，
     * 不走标准拉丁编码表，规范要求这样标。度量按 unitsPerEm 换算到千分之一 em。
     */
    private static String descriptor(PdfFontUse font, int program) {
        PdfFontProgram source = font.program();
        int[] bbox = source.boundingBox();
        double scale = 1000.0 / source.unitsPerEm();
        return "<< /Type /FontDescriptor /FontName /" + font.baseFont() + " /Flags 4 /FontBBox ["
                + Math.round(bbox[0] * scale) + " " + Math.round(bbox[1] * scale) + " "
                + Math.round(bbox[2] * scale) + " " + Math.round(bbox[3] * scale)
                + "] /ItalicAngle 0 /Ascent " + Math.round(source.ascender() * scale)
                + " /Descent " + Math.round(source.descender() * scale)
                + " /CapHeight " + Math.round(source.ascender() * scale)
                + " /StemV 80 /FontFile2 " + program + " 0 R >>";
    }
}
