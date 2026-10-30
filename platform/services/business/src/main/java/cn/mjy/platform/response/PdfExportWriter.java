package cn.mjy.platform.response;

import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.Calendar;
import java.util.GregorianCalendar;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.TimeZone;
import org.apache.pdfbox.cos.COSName;
import org.apache.pdfbox.io.IOUtils;
import org.apache.pdfbox.pdmodel.PDDocument;
import org.apache.pdfbox.pdmodel.PDDocumentInformation;
import org.apache.pdfbox.pdmodel.PDPage;
import org.apache.pdfbox.pdmodel.PDPageContentStream;
import org.apache.pdfbox.pdmodel.common.PDRectangle;
import org.apache.pdfbox.pdmodel.common.PDStream;
import org.apache.pdfbox.pdmodel.font.PDType0Font;

/**
 * 逐份答卷成文的 PDF（R06-05）。每份答卷一页，直接消费与 DOCX 相同的冻结记录视图，
 * 因而继承既有作业的快照、字段脱敏、失败恢复与下载再授权语义。
 *
 * <p>字体使用随应用打包并按 SIL OFL 许可分发的 Noto Sans SC TrueType；PDF 内嵌使用到的字形，
 * 不依赖宿主机或阅读器的中文字体。许可证位于同目录 {@code fonts/OFL.txt}。
 */
final class PdfExportWriter implements ExportFormat.Writer {

    static final long MAX_RESPONSES = 10_000L;
    private static final String FONT_RESOURCE = "/fonts/NotoSansSC-Variable.ttf";
    private static final float MARGIN = 42f;
    private static final float BODY_SIZE = 9f;
    private static final float TITLE_SIZE = 15f;
    private static final float SECTION_SIZE = 11f;
    private static final float LEADING = 13f;

    @Override
    public long maxDataRows() {
        return MAX_RESPONSES;
    }

    @Override
    public void write(ExportContent content, OutputStream out) throws IOException {
        write(content, out, ExportWriteBudget.unlimited());
    }

    @Override
    public void write(ExportContent content, OutputStream out, ExportWriteBudget budget) throws IOException {
        try (PDDocument document = new PDDocument(IOUtils.createTempFileOnlyStreamCache());
                InputStream fontBytes = PdfExportWriter.class.getResourceAsStream(FONT_RESOURCE)) {
            if (fontBytes == null) {
                throw new IOException("embedded PDF font is missing: " + FONT_RESOURCE);
            }
            deterministicMetadata(document);
            // PDFBox 对可变 CJK 字体的自动子集会丢失 cmap；完整内嵌仍是自包含且许可允许的正确产物。
            PDType0Font font = PDType0Font.load(document, fontBytes, false);
            Map<Integer, Integer> unicodeByCid = new LinkedHashMap<>();
            content.records().forEach(record -> {
                budget.checkpoint();
                page(document, font, content, record, unicodeByCid, budget);
            });
            budget.checkpoint();
            installToUnicode(document, font, unicodeByCid);
            document.save(out);
        }
    }

    private static void deterministicMetadata(PDDocument document) {
        Calendar epoch = new GregorianCalendar(TimeZone.getTimeZone("UTC"));
        epoch.clear();
        epoch.set(2000, Calendar.JANUARY, 1, 0, 0, 0);
        PDDocumentInformation information = new PDDocumentInformation();
        information.setProducer("Star Survey");
        information.setCreator("Star Survey");
        information.setCreationDate(epoch);
        information.setModificationDate(epoch);
        document.setDocumentInformation(information);
        document.setDocumentId(0L);
    }

    private static void page(PDDocument document, PDType0Font font, ExportContent content, ExportRecord record,
            Map<Integer, Integer> unicodeByCid, ExportWriteBudget budget) throws IOException {
        try (PageWriter pages = new PageWriter(document, font, unicodeByCid)) {
            for (Line line : lines(content, record)) {
                budget.checkpoint();
                for (String wrapped : wrap(font, line.size(), line.text(), PDRectangle.A4.getWidth() - 2 * MARGIN)) {
                    pages.line(line.size(), wrapped);
                }
            }
        }
    }

    private static void writeLine(PDPageContentStream stream, PDType0Font font, float size, String text, float y)
            throws IOException {
        stream.beginText();
        stream.setFont(font, size);
        stream.newLineAtOffset(MARGIN, y);
        stream.showText(clean(text));
        stream.endText();
    }

    /**
     * FontBox 对共用字形有时选择 CJK radical 的兼容码位作为反向映射。显式以写入时的原始 Unicode
     * 生成 ToUnicode，确保复制、搜索和无障碍读取获得“意见”而不是外观相同的部首码位。
     */
    private static void installToUnicode(PDDocument document, PDType0Font font, Map<Integer, Integer> unicodeByCid)
            throws IOException {
        StringBuilder cmap = new StringBuilder("/CIDInit /ProcSet findresource begin\n12 dict begin\n"
                + "begincmap\n/CIDSystemInfo << /Registry (Adobe) /Ordering (UCS) /Supplement 0 >> def\n"
                + "/CMapName /StarSurveyUnicode def\n/CMapType 2 def\n"
                + "1 begincodespacerange\n<0000> <FFFF>\nendcodespacerange\n");
        List<Map.Entry<Integer, Integer>> entries = new ArrayList<>(unicodeByCid.entrySet());
        for (int from = 0; from < entries.size(); from += 100) {
            int to = Math.min(from + 100, entries.size());
            cmap.append(to - from).append(" beginbfchar\n");
            for (Map.Entry<Integer, Integer> entry : entries.subList(from, to)) {
                cmap.append('<').append(String.format("%04X", entry.getKey())).append("> <")
                        .append(utf16Hex(entry.getValue())).append(">\n");
            }
            cmap.append("endbfchar\n");
        }
        cmap.append("endcmap\nCMapName currentdict /CMap defineresource pop\nend\nend\n");
        PDStream stream = new PDStream(document);
        try (OutputStream output = stream.createOutputStream(COSName.FLATE_DECODE)) {
            output.write(cmap.toString().getBytes(StandardCharsets.US_ASCII));
        }
        font.getCOSObject().setItem(COSName.TO_UNICODE, stream);
    }

    private static String utf16Hex(int codePoint) {
        byte[] bytes = new String(Character.toChars(codePoint)).getBytes(StandardCharsets.UTF_16BE);
        StringBuilder hex = new StringBuilder(bytes.length * 2);
        for (byte value : bytes) {
            hex.append(String.format("%02X", value & 0xff));
        }
        return hex.toString();
    }

    private static void rememberUnicode(PDType0Font font, String text, Map<Integer, Integer> unicodeByCid)
            throws IOException {
        String value = clean(text);
        for (int offset = 0; offset < value.length();) {
            int codePoint = value.codePointAt(offset);
            byte[] encoded = font.encode(new String(Character.toChars(codePoint)));
            if (encoded.length == 2) {
                int cid = (encoded[0] & 0xff) << 8 | encoded[1] & 0xff;
                unicodeByCid.putIfAbsent(cid, codePoint);
            }
            offset += Character.charCount(codePoint);
        }
    }

    private static List<Line> lines(ExportContent content, ExportRecord record) {
        List<Line> result = new ArrayList<>();
        result.add(new Line(TITLE_SIZE, "答卷 " + cell(record.cells(), 2)));
        int fixed = Math.min(ExportLayout.FIXED_CODES.size(), record.cells().size());
        result.add(new Line(SECTION_SIZE, "答卷信息"));
        labelValues(result, content, record, 0, fixed);
        result.add(new Line(SECTION_SIZE, "作答"));
        labelValues(result, content, record, fixed, record.cells().size());
        if (!record.extensions().isEmpty()) {
            result.add(new Line(SECTION_SIZE, "结构化作答"));
            for (List<String> extension : record.extensions()) {
                result.add(new Line(BODY_SIZE, String.join(" ｜ ", extension.stream().map(PdfExportWriter::clean).toList())));
            }
        }
        if (!record.attachments().isEmpty()) {
            result.add(new Line(SECTION_SIZE, "附件"));
            for (List<String> attachment : record.attachments()) {
                result.add(new Line(BODY_SIZE,
                        String.join(" ｜ ", attachment.stream().map(PdfExportWriter::clean).toList())));
            }
        }
        return result;
    }

    private static void labelValues(List<Line> lines, ExportContent content, ExportRecord record, int from, int to) {
        for (int i = from; i < to; i++) {
            String label = i < content.labels().size() ? content.labels().get(i)
                    : i < content.codes().size() ? content.codes().get(i) : "";
            lines.add(new Line(BODY_SIZE, clean(label) + "：" + clean(cell(record.cells(), i))));
        }
    }

    private static List<String> wrap(PDType0Font font, float size, String text, float maxWidth) throws IOException {
        String value = clean(text);
        if (value.isEmpty()) {
            return List.of(" ");
        }
        List<String> result = new ArrayList<>();
        for (String paragraph : value.split("\\n", -1)) {
            StringBuilder line = new StringBuilder();
            for (int offset = 0; offset < paragraph.length();) {
                int codePoint = paragraph.codePointAt(offset);
                String next = new String(Character.toChars(codePoint));
                String candidate = line + next;
                if (!line.isEmpty() && font.getStringWidth(candidate) / 1000f * size > maxWidth) {
                    result.add(line.toString());
                    line.setLength(0);
                }
                line.append(next);
                offset += Character.charCount(codePoint);
            }
            result.add(line.isEmpty() ? " " : line.toString());
        }
        return result;
    }

    private static String cell(List<String> cells, int index) {
        return index < cells.size() && cells.get(index) != null ? cells.get(index) : "";
    }

    private static String clean(String value) {
        if (value == null) {
            return "";
        }
        StringBuilder cleaned = new StringBuilder(value.length());
        value.codePoints().filter(cp -> cp == '\n' || cp == '\t' || cp >= 0x20 && cp != 0x7f)
                .forEach(cleaned::appendCodePoint);
        return ExportCellGuard.guard(cleaned.toString());
    }

    private record Line(float size, String text) {
    }

    /** 一份答卷从新页开始，超长内容自动续页，绝不截断。 */
    private static final class PageWriter implements AutoCloseable {
        private final PDDocument document;
        private final PDType0Font font;
        private final Map<Integer, Integer> unicodeByCid;
        private PDPageContentStream stream;
        private float y;

        private PageWriter(PDDocument document, PDType0Font font, Map<Integer, Integer> unicodeByCid)
                throws IOException {
            this.document = document;
            this.font = font;
            this.unicodeByCid = unicodeByCid;
            newPage();
        }

        private void line(float size, String text) throws IOException {
            if (y < MARGIN) {
                newPage();
            }
            rememberUnicode(font, text, unicodeByCid);
            writeLine(stream, font, size, text, y);
            y -= Math.max(LEADING, size + 3f);
        }

        private void newPage() throws IOException {
            if (stream != null) {
                stream.close();
            }
            PDPage page = new PDPage(PDRectangle.A4);
            document.addPage(page);
            stream = new PDPageContentStream(document, page);
            y = PDRectangle.A4.getHeight() - MARGIN;
        }

        @Override
        public void close() throws IOException {
            stream.close();
        }
    }
}
