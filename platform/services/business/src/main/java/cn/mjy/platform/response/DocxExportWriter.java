package cn.mjy.platform.response;

import java.io.BufferedWriter;
import java.io.IOException;
import java.io.OutputStream;
import java.io.OutputStreamWriter;
import java.io.Writer;
import java.nio.charset.StandardCharsets;
import java.util.List;

/**
 * 只用 JDK 写出的最小 DOCX（OOXML WordprocessingML）：逐份答卷成文（R06-05 / R06-06）。
 * 每份答卷一页，页内依次是「答卷信息」「作答」「结构化作答」「附件」，
 * 题目标签与作答成对排版——这是文档格式与表格格式的根本区别：表格按列出，文档按份出。
 *
 * <p><b>副表作答在这里被还原成真正的表格。</b>导出的 {@code extensions} 长表是一个单元格一行
 * （ADR 0015 增补二），因为表格格式的列集必须在冻结表头时就定死；文档格式没有这个约束——
 * 它一次只持有一份答卷（{@link ExportRecord}），这一份的列代码是现成的，透视得起。
 *
 * <p>不嵌字体：Word 用系统中文字体排版。PDF 不能这么办（见 ADR 0015 增补三「没有做的」）。
 *
 * <p>防公式注入照旧走 {@link ExportCellGuard}：Word 自己不算公式，但表格可以整块粘进 Excel，
 * 那一步又回到会执行公式的地方——与 SAV 同一条理由，不按格式开口子。
 */
final class DocxExportWriter implements ExportFormat.Writer {

    /**
     * 一份文档里最多放多少份答卷。30 万答卷的 Word 文档没人打得开，超出时作业失败而不是产出
     * 一份打不开的文件（与 XLSX 超出 Excel 行数上限同一个处置）。
     */
    static final long MAX_RESPONSES = 10_000L;

    /** 附件表的表头；列取自 {@link ExportLayout#ATTACHMENT_HEADER} 的后六列（前几列在答卷信息里）。 */
    private static final List<String> ATTACHMENT_COLUMNS = List.of("序号", "名称", "大小", "类型", "存储名", "sha256");
    private static final int ATTACHMENT_FROM = 5;

    /** {@link ExportLayout#FIXED_CODES} 里 {@code responseid} 的下标。 */
    private static final int RESPONSE_ID = 2;

    private static final String XML_DECL = "<?xml version=\"1.0\" encoding=\"UTF-8\" standalone=\"yes\"?>\n";
    private static final String NS_W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main";
    private static final String NS_PKG_REL = "http://schemas.openxmlformats.org/package/2006/relationships";
    private static final String REL_TYPE = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/";

    @Override
    public long maxDataRows() {
        return MAX_RESPONSES;
    }

    @Override
    public void write(ExportContent content, OutputStream out) throws IOException {
        try (ExportZip zip = new ExportZip(out)) {
            text(zip, "[Content_Types].xml", contentTypes());
            text(zip, "_rels/.rels", rootRels());
            text(zip, "docProps/app.xml", appProps());
            try (OutputStream entry = zip.entry("word/document.xml")) {
                document(content, entry);
            }
            text(zip, "word/_rels/document.xml.rels", documentRels());
            text(zip, "word/styles.xml", styles());
        }
    }

    private static void text(ExportZip zip, String name, String content) throws IOException {
        try (OutputStream entry = zip.entry(name)) {
            entry.write(content.getBytes(StandardCharsets.UTF_8));
        }
    }

    /** 逐份答卷流式写出：任何时刻只持有一份答卷，内存占用与总答卷数无关。 */
    private static void document(ExportContent content, OutputStream out) throws IOException {
        Writer writer = new BufferedWriter(new OutputStreamWriter(out, StandardCharsets.UTF_8));
        writer.write(XML_DECL);
        writer.write("<w:document xmlns:w=\"" + NS_W + "\"><w:body>");
        int[] written = {0};
        content.records().forEach(record -> {
            if (written[0]++ > 0) {
                writer.write("<w:p><w:r><w:br w:type=\"page\"/></w:r></w:p>");
            }
            section(writer, content, record);
        });
        writer.write("<w:sectPr/></w:body></w:document>");
        writer.flush();
    }

    private static void section(Writer writer, ExportContent content, ExportRecord record) throws IOException {
        List<String> cells = record.cells();
        heading(writer, 1, "答卷 " + cell(cells, RESPONSE_ID));
        int fixed = ExportLayout.FIXED_CODES.size();
        heading(writer, 2, "答卷信息");
        labelValues(writer, content, record, 0, Math.min(fixed, cells.size()));
        heading(writer, 2, "作答");
        labelValues(writer, content, record, Math.min(fixed, cells.size()), cells.size());
        ExtensionTables.write(writer, content, record);
        attachments(writer, record);
    }

    /** 「标签｜值」两列表；标签取答卷表表头第二行，值取这一份答卷的那一格。 */
    private static void labelValues(Writer writer, ExportContent content, ExportRecord record, int from, int to)
            throws IOException {
        if (from >= to) {
            return;
        }
        tableStart(writer);
        for (int i = from; i < to; i++) {
            String label = i < content.labels().size() ? content.labels().get(i)
                    : i < content.codes().size() ? content.codes().get(i) : "";
            row(writer, List.of(label, cell(record.cells(), i)));
        }
        tableEnd(writer);
    }

    private static void attachments(Writer writer, ExportRecord record) throws IOException {
        if (record.attachments().isEmpty()) {
            return;
        }
        heading(writer, 2, "附件");
        tableStart(writer);
        row(writer, ATTACHMENT_COLUMNS);
        for (List<String> attachment : record.attachments()) {
            row(writer, attachment.subList(ATTACHMENT_FROM,
                    Math.min(ATTACHMENT_FROM + ATTACHMENT_COLUMNS.size(), attachment.size())));
        }
        tableEnd(writer);
    }

    // ---- 排版原语 ----

    static void heading(Writer writer, int level, String text) throws IOException {
        writer.write("<w:p><w:pPr><w:pStyle w:val=\"Heading" + level + "\"/></w:pPr>");
        run(writer, text);
        writer.write("</w:p>");
    }

    static void paragraph(Writer writer, String text) throws IOException {
        writer.write("<w:p>");
        run(writer, text);
        writer.write("</w:p>");
    }

    static void tableStart(Writer writer) throws IOException {
        writer.write("<w:tbl><w:tblPr><w:tblW w:w=\"0\" w:type=\"auto\"/><w:tblBorders>"
                + "<w:top w:val=\"single\" w:sz=\"4\" w:color=\"auto\"/>"
                + "<w:left w:val=\"single\" w:sz=\"4\" w:color=\"auto\"/>"
                + "<w:bottom w:val=\"single\" w:sz=\"4\" w:color=\"auto\"/>"
                + "<w:right w:val=\"single\" w:sz=\"4\" w:color=\"auto\"/>"
                + "<w:insideH w:val=\"single\" w:sz=\"4\" w:color=\"auto\"/>"
                + "<w:insideV w:val=\"single\" w:sz=\"4\" w:color=\"auto\"/>"
                + "</w:tblBorders></w:tblPr>");
    }

    /** 表后补一个空段落：两张紧挨着的表在 Word 里会被并成一张。 */
    static void tableEnd(Writer writer) throws IOException {
        writer.write("</w:tbl><w:p/>");
    }

    static void row(Writer writer, List<String> cells) throws IOException {
        writer.write("<w:tr>");
        for (String value : cells) {
            writer.write("<w:tc><w:tcPr><w:tcW w:w=\"0\" w:type=\"auto\"/></w:tcPr>");
            String guarded = ExportCellGuard.guard(value);
            if (guarded.isEmpty()) {
                writer.write("<w:p/>");
            } else {
                paragraph(writer, guarded);
            }
            writer.write("</w:tc>");
        }
        writer.write("</w:tr>");
    }

    /** 一个文本运行；换行写成 {@code <w:br/>}，否则 Word 会把整段挤成一行。 */
    private static void run(Writer writer, String text) throws IOException {
        writer.write("<w:r>");
        String[] lines = text.split("\n", -1);
        for (int i = 0; i < lines.length; i++) {
            if (i > 0) {
                writer.write("<w:br/>");
            }
            writer.write("<w:t xml:space=\"preserve\">" + ExportXml.escape(lines[i]) + "</w:t>");
        }
        writer.write("</w:r>");
    }

    private static String cell(List<String> cells, int index) {
        String value = index < cells.size() ? cells.get(index) : null;
        return value == null ? "" : value;
    }

    // ---- 包的骨架 ----

    private static String contentTypes() {
        return XML_DECL + "<Types xmlns=\"http://schemas.openxmlformats.org/package/2006/content-types\">"
                + "<Default Extension=\"rels\" ContentType=\"application/vnd.openxmlformats-package.relationships+xml\"/>"
                + "<Default Extension=\"xml\" ContentType=\"application/xml\"/>"
                + "<Override PartName=\"/word/document.xml\" ContentType=\"application/vnd.openxmlformats-"
                + "officedocument.wordprocessingml.document.main+xml\"/>"
                + "<Override PartName=\"/word/styles.xml\" ContentType=\"application/vnd.openxmlformats-"
                + "officedocument.wordprocessingml.styles+xml\"/>"
                + "<Override PartName=\"/docProps/app.xml\" ContentType=\"application/vnd.openxmlformats-"
                + "officedocument.extended-properties+xml\"/>"
                + "</Types>";
    }

    private static String rootRels() {
        return XML_DECL + "<Relationships xmlns=\"" + NS_PKG_REL + "\">"
                + "<Relationship Id=\"rId1\" Type=\"" + REL_TYPE + "officeDocument\" Target=\"word/document.xml\"/>"
                + "<Relationship Id=\"rId2\" Type=\"" + REL_TYPE + "extended-properties\" Target=\"docProps/app.xml\"/>"
                + "</Relationships>";
    }

    private static String documentRels() {
        return XML_DECL + "<Relationships xmlns=\"" + NS_PKG_REL + "\">"
                + "<Relationship Id=\"rId1\" Type=\"" + REL_TYPE + "styles\" Target=\"styles.xml\"/>"
                + "</Relationships>";
    }

    private static String appProps() {
        return XML_DECL + "<Properties xmlns=\"http://schemas.openxmlformats.org/officeDocument/2006/"
                + "extended-properties\"><Application>MJY Platform</Application></Properties>";
    }

    private static String styles() {
        return XML_DECL + "<w:styles xmlns:w=\"" + NS_W + "\">"
                + "<w:docDefaults><w:rPrDefault><w:rPr><w:sz w:val=\"21\"/></w:rPr></w:rPrDefault>"
                + "<w:pPrDefault/></w:docDefaults>"
                + "<w:style w:type=\"paragraph\" w:default=\"1\" w:styleId=\"Normal\">"
                + "<w:name w:val=\"Normal\"/></w:style>"
                + "<w:style w:type=\"paragraph\" w:styleId=\"Heading1\"><w:name w:val=\"heading 1\"/>"
                + "<w:pPr><w:outlineLvl w:val=\"0\"/></w:pPr><w:rPr><w:b/><w:sz w:val=\"32\"/></w:rPr></w:style>"
                + "<w:style w:type=\"paragraph\" w:styleId=\"Heading2\"><w:name w:val=\"heading 2\"/>"
                + "<w:pPr><w:outlineLvl w:val=\"1\"/></w:pPr><w:rPr><w:b/><w:sz w:val=\"26\"/></w:rPr></w:style>"
                + "</w:styles>";
    }
}
