package cn.mjy.platform.response;

import java.io.BufferedWriter;
import java.io.IOException;
import java.io.OutputStream;
import java.io.OutputStreamWriter;
import java.io.Writer;
import java.nio.charset.StandardCharsets;
import java.util.List;

/**
 * 只用 JDK 写出的最小 XLSX（OOXML SpreadsheetML）：内容类型、包关系、应用属性、工作簿及其关系、最小样式、
 * 每张表一个工作表。单元格一律是内联字符串（{@code t="inlineStr"}）——从不产生公式，也不需要共享字符串表，
 * 行可以流式写出。省略行号与单元格引用（规范允许，按顺序排列）。
 *
 * <p>XML 1.0 不允许的控制字符被丢弃；超过 Excel 单元格上限 32767 字符的值截断。
 */
final class XlsxExportWriter implements ExportFormat.Writer {

    /** Excel 的行数上限（含表头两行）。 */
    static final long MAX_SHEET_ROWS = 1_048_576L;
    static final int MAX_CELL_CHARS = 32_767;
    private static final int HEADER_ROWS = 2;

    private static final String XML_DECL = "<?xml version=\"1.0\" encoding=\"UTF-8\" standalone=\"yes\"?>\n";
    private static final String NS_MAIN = "http://schemas.openxmlformats.org/spreadsheetml/2006/main";
    private static final String NS_REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships";
    private static final String NS_PKG_REL = "http://schemas.openxmlformats.org/package/2006/relationships";
    private static final String REL_TYPE = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/";

    @Override
    public long maxDataRows() {
        return MAX_SHEET_ROWS - HEADER_ROWS;
    }

    @Override
    public void write(ExportContent content, OutputStream out) throws IOException {
        List<ExportSheet> sheets = content.sheets();
        try (ExportZip zip = new ExportZip(out)) {
            text(zip, "[Content_Types].xml", contentTypes(sheets.size()));
            text(zip, "_rels/.rels", rootRels());
            text(zip, "docProps/app.xml", appProps());
            text(zip, "xl/workbook.xml", workbook(sheets));
            text(zip, "xl/_rels/workbook.xml.rels", workbookRels(sheets.size()));
            text(zip, "xl/styles.xml", styles());
            for (int i = 0; i < sheets.size(); i++) {
                try (OutputStream entry = zip.entry("xl/worksheets/sheet" + (i + 1) + ".xml")) {
                    writeSheet(sheets.get(i), entry);
                }
            }
        }
    }

    private static void text(ExportZip zip, String name, String content) throws IOException {
        try (OutputStream entry = zip.entry(name)) {
            entry.write(content.getBytes(StandardCharsets.UTF_8));
        }
    }

    private static void writeSheet(ExportSheet sheet, OutputStream entry) throws IOException {
        Writer writer = new BufferedWriter(new OutputStreamWriter(entry, StandardCharsets.UTF_8));
        writer.write(XML_DECL);
        writer.write("<worksheet xmlns=\"" + NS_MAIN + "\" xmlns:r=\"" + NS_REL + "\"><sheetData>");
        sheet.rows().forEach(cells -> writeRow(writer, cells));
        writer.write("</sheetData></worksheet>");
        writer.flush();
    }

    private static void writeRow(Writer writer, List<String> cells) throws IOException {
        writer.write("<row>");
        for (String cell : cells) {
            if (cell == null || cell.isEmpty()) {
                writer.write("<c/>");
                continue;
            }
            writer.write("<c t=\"inlineStr\"><is><t xml:space=\"preserve\">");
            writer.write(ExportXml.escape(truncate(cell)));
            writer.write("</t></is></c>");
        }
        writer.write("</row>");
    }

    private static String truncate(String value) {
        if (value.length() <= MAX_CELL_CHARS) {
            return value;
        }
        int end = MAX_CELL_CHARS;
        if (Character.isHighSurrogate(value.charAt(end - 1))) {
            end--;
        }
        return value.substring(0, end);
    }

    private static String contentTypes(int sheets) {
        StringBuilder xml = new StringBuilder(XML_DECL)
                .append("<Types xmlns=\"http://schemas.openxmlformats.org/package/2006/content-types\">")
                .append("<Default Extension=\"rels\" ContentType=\"application/vnd.openxmlformats-package.relationships+xml\"/>")
                .append("<Default Extension=\"xml\" ContentType=\"application/xml\"/>")
                .append("<Override PartName=\"/xl/workbook.xml\" ContentType=\"application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml\"/>")
                .append("<Override PartName=\"/xl/styles.xml\" ContentType=\"application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml\"/>")
                .append("<Override PartName=\"/docProps/app.xml\" ContentType=\"application/vnd.openxmlformats-officedocument.extended-properties+xml\"/>");
        for (int i = 1; i <= sheets; i++) {
            xml.append("<Override PartName=\"/xl/worksheets/sheet").append(i)
                    .append(".xml\" ContentType=\"application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml\"/>");
        }
        return xml.append("</Types>").toString();
    }

    private static String rootRels() {
        return XML_DECL + "<Relationships xmlns=\"" + NS_PKG_REL + "\">"
                + "<Relationship Id=\"rId1\" Type=\"" + REL_TYPE + "officeDocument\" Target=\"xl/workbook.xml\"/>"
                + "<Relationship Id=\"rId2\" Type=\"" + REL_TYPE + "extended-properties\" Target=\"docProps/app.xml\"/>"
                + "</Relationships>";
    }

    private static String appProps() {
        return XML_DECL + "<Properties xmlns=\"http://schemas.openxmlformats.org/officeDocument/2006/extended-properties\">"
                + "<Application>MJY Platform</Application></Properties>";
    }

    private static String workbook(List<ExportSheet> sheets) {
        StringBuilder xml = new StringBuilder(XML_DECL)
                .append("<workbook xmlns=\"").append(NS_MAIN).append("\" xmlns:r=\"").append(NS_REL).append("\"><sheets>");
        for (int i = 0; i < sheets.size(); i++) {
            xml.append("<sheet name=\"").append(ExportXml.escape(sheets.get(i).title())).append("\" sheetId=\"").append(i + 1)
                    .append("\" r:id=\"rId").append(i + 1).append("\"/>");
        }
        return xml.append("</sheets></workbook>").toString();
    }

    /** 工作表的关系 id 为 rId1..rIdN，样式紧随其后。 */
    private static String workbookRels(int sheets) {
        StringBuilder xml = new StringBuilder(XML_DECL).append("<Relationships xmlns=\"").append(NS_PKG_REL).append("\">");
        for (int i = 1; i <= sheets; i++) {
            xml.append("<Relationship Id=\"rId").append(i).append("\" Type=\"").append(REL_TYPE)
                    .append("worksheet\" Target=\"worksheets/sheet").append(i).append(".xml\"/>");
        }
        xml.append("<Relationship Id=\"rId").append(sheets + 1).append("\" Type=\"").append(REL_TYPE)
                .append("styles\" Target=\"styles.xml\"/>");
        return xml.append("</Relationships>").toString();
    }

    private static String styles() {
        return XML_DECL + "<styleSheet xmlns=\"" + NS_MAIN + "\">"
                + "<fonts count=\"1\"><font><sz val=\"11\"/><name val=\"Calibri\"/></font></fonts>"
                + "<fills count=\"2\"><fill><patternFill patternType=\"none\"/></fill>"
                + "<fill><patternFill patternType=\"gray125\"/></fill></fills>"
                + "<borders count=\"1\"><border><left/><right/><top/><bottom/><diagonal/></border></borders>"
                + "<cellStyleXfs count=\"1\"><xf numFmtId=\"0\" fontId=\"0\" fillId=\"0\" borderId=\"0\"/></cellStyleXfs>"
                + "<cellXfs count=\"1\"><xf numFmtId=\"0\" fontId=\"0\" fillId=\"0\" borderId=\"0\" xfId=\"0\"/></cellXfs>"
                + "<cellStyles count=\"1\"><cellStyle name=\"Normal\" xfId=\"0\" builtinId=\"0\"/></cellStyles>"
                + "</styleSheet>";
    }
}
