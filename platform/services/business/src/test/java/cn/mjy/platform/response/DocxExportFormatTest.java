package cn.mjy.platform.response;

import static org.assertj.core.api.Assertions.assertThat;

import java.io.ByteArrayInputStream;
import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.zip.ZipEntry;
import java.util.zip.ZipInputStream;
import javax.xml.parsers.DocumentBuilder;
import javax.xml.parsers.DocumentBuilderFactory;
import org.junit.jupiter.api.Test;
import org.w3c.dom.Document;
import org.w3c.dom.Element;
import org.w3c.dom.Node;
import org.w3c.dom.NodeList;

/**
 * 纯单元：逐份答卷成文的 Word 文档（R06-05 / R06-06）。每份答卷一页、题目与作答成对；
 * 副表作答在这里<b>被还原成真正的表格</b>——文档格式一次只持有一份答卷，透视得起。
 */
class DocxExportFormatTest {

    private static final List<String> CODES = List.of("version", "sid", "responseid", "generation", "state",
            "startedat", "completedat", "answersstatus", "QSINGLE", "QTABLE");
    private static final List<String> LABELS = List.of("版本", "引擎问卷号", "答卷号", "代次", "状态", "开始时间",
            "完成时间", "作答状态", "满意度", "随行物品 [结构化作答]");

    private static final ExportRecord FIRST = new ExportRecord(
            Arrays.asList("1", "900001", "7", "gen-a", "engine_completed", "2026-09-24T00:00:00Z", null,
                    "available", "=cmd|' /C calc'!A0", "[{\"item\":\"甲\"}]"),
            List.of(List.of("1", "900001", "7", "QFILE", "U900", "1", "证件.png", "12.5", "png", "fu_1",
                    "a".repeat(64))),
            List.of(cell("7", "QTABLE", "rt1", "true", "0", "item", "甲"),
                    cell("7", "QTABLE", "rt1", "true", "0", "qty", "2"),
                    cell("7", "QTABLE", "rt1", "true", "1", "item", "乙"),
                    cell("7", "QTABLE", "rt1", "true", "1", "qty", "3")));

    private static final ExportRecord SECOND = new ExportRecord(
            Arrays.asList("1", "900001", "8", "gen-a", "engine_completed", null, null, "available", null, null),
            List.of(),
            List.of(cell("8", "QTABLE", "rt1", "false", "", "", "")));

    private static List<String> cell(String responseId, String question, String version, String valid,
            String rowIndex, String column, String value) {
        return List.of("1", "900001", responseId, "gen-a", question, version, valid, rowIndex, column, value);
    }

    private static ExportContent content(ExportRecord... records) {
        ExportSheet responses = new ExportSheet("responses", "答卷", 2, List.of(), sink -> {
            sink.accept(CODES);
            sink.accept(LABELS);
            for (ExportRecord record : records) {
                sink.accept(record.cells());
            }
        });
        return new ExportContent(List.of(responses), CODES, LABELS, sink -> {
            for (ExportRecord record : records) {
                sink.accept(record);
            }
        });
    }

    private static byte[] write(ExportRecord... records) throws IOException {
        ByteArrayOutputStream out = new ByteArrayOutputStream();
        ExportFormat.DOCX.writer().write(content(records), out);
        return out.toByteArray();
    }

    private static Map<String, byte[]> unzip(byte[] zip) throws IOException {
        Map<String, byte[]> entries = new LinkedHashMap<>();
        try (ZipInputStream in = new ZipInputStream(new ByteArrayInputStream(zip))) {
            ZipEntry entry;
            while ((entry = in.getNextEntry()) != null) {
                entries.put(entry.getName(), in.readAllBytes());
            }
        }
        return entries;
    }

    private static Document parse(byte[] xml) throws Exception {
        DocumentBuilderFactory factory = DocumentBuilderFactory.newInstance();
        factory.setNamespaceAware(true);
        factory.setFeature("http://apache.org/xml/features/disallow-doctype-decl", true);
        DocumentBuilder builder = factory.newDocumentBuilder();
        return builder.parse(new ByteArrayInputStream(xml));
    }

    private static Document document(byte[] docx) throws Exception {
        return parse(unzip(docx).get("word/document.xml"));
    }

    private static List<String> texts(Node scope) {
        List<String> texts = new ArrayList<>();
        NodeList all = ((Element) scope).getElementsByTagNameNS("*", "t");
        for (int i = 0; i < all.getLength(); i++) {
            texts.add(all.item(i).getTextContent());
        }
        return texts;
    }

    private static int pageBreaks(Document document) {
        NodeList breaks = document.getElementsByTagNameNS("*", "br");
        int pages = 0;
        for (int i = 0; i < breaks.getLength(); i++) {
            Node type = breaks.item(i).getAttributes()
                    .getNamedItemNS("http://schemas.openxmlformats.org/wordprocessingml/2006/main", "type");
            pages += type != null && "page".equals(type.getNodeValue()) ? 1 : 0;
        }
        return pages;
    }

    /** 表格逐行的单元格文本。 */
    private static List<List<String>> table(Document document, int index) {
        NodeList tables = document.getElementsByTagNameNS("*", "tbl");
        Element table = (Element) tables.item(index);
        List<List<String>> rows = new ArrayList<>();
        NodeList trs = table.getElementsByTagNameNS("*", "tr");
        for (int i = 0; i < trs.getLength(); i++) {
            List<String> cells = new ArrayList<>();
            NodeList tcs = ((Element) trs.item(i)).getElementsByTagNameNS("*", "tc");
            for (int j = 0; j < tcs.getLength(); j++) {
                cells.add(String.join("", texts(tcs.item(j))));
            }
            rows.add(cells);
        }
        return rows;
    }

    @Test
    void theDocxIsAZipOfWellFormedPartsWithEveryRequiredPart() throws Exception {
        Map<String, byte[]> parts = unzip(write(FIRST, SECOND));

        assertThat(parts.keySet()).containsExactly("[Content_Types].xml", "_rels/.rels", "docProps/app.xml",
                "word/document.xml", "word/_rels/document.xml.rels", "word/styles.xml");
        for (Map.Entry<String, byte[]> part : parts.entrySet()) {
            parse(part.getValue());
        }
        String types = new String(parts.get("[Content_Types].xml"), StandardCharsets.UTF_8);
        assertThat(types).contains("/word/document.xml", "/word/styles.xml");
    }

    /** 每份答卷一个标题、一页；末尾不留多余的分页符。 */
    @Test
    void eachResponseGetsItsOwnHeadingAndPage() throws Exception {
        Document document = document(write(FIRST, SECOND));

        assertThat(texts(document.getDocumentElement())).contains("答卷 7", "答卷 8");
        assertThat(pageBreaks(document)).as("两份答卷之间一个分页符，最后一份之后没有").isEqualTo(1);
    }

    /** 固定列与题目分两张表：先「答卷信息」，再「作答」，每行「标签｜值」。 */
    @Test
    void fixedColumnsAndAnswersAreTwoLabelValueTables() throws Exception {
        Document document = document(write(FIRST));

        assertThat(table(document, 0)).contains(List.of("版本", "1"), List.of("答卷号", "7"),
                List.of("作答状态", "available"), List.of("完成时间", ""));
        assertThat(table(document, 1)).contains(List.of("满意度", "'=cmd|' /C calc'!A0"));
    }

    /** 副表作答还原成真正的表格：第一行是列代码，之后一行一个行序。 */
    @Test
    void sideTableAnswersArePivotedBackIntoARealTable() throws Exception {
        Document document = document(write(FIRST));

        assertThat(texts(document.getDocumentElement())).contains("随行物品 [结构化作答]（结构版本 rt1）");
        assertThat(table(document, 2)).containsExactly(
                List.of("行", "item", "qty"),
                List.of("1", "甲", "2"),
                List.of("2", "乙", "3"));
    }

    /** 闸门没过的那一题不是「没填」：文档里明说它不可信，且没有数据表。 */
    @Test
    void aQuestionRejectedByThePluginGateSaysSoInsteadOfLookingBlank() throws Exception {
        Document document = document(write(SECOND));

        assertThat(texts(document.getDocumentElement()))
                .anySatisfy(text -> assertThat(text).contains("未通过校验"));
        assertThat(document.getElementsByTagNameNS("*", "tbl").getLength())
                .as("只有答卷信息与作答两张表，没有副表数据表").isEqualTo(2);
    }

    @Test
    void attachmentsAreListedWithoutTouchingAnyUrl() throws Exception {
        Document document = document(write(FIRST));

        List<List<String>> attachments = table(document, 3);
        assertThat(attachments.getFirst()).containsExactly("序号", "名称", "大小", "类型", "存储名", "sha256");
        assertThat(attachments.get(1)).containsExactly("1", "证件.png", "12.5", "png", "fu_1", "a".repeat(64));
    }

    @Test
    void theSameInputProducesByteIdenticalFiles() throws IOException {
        assertThat(write(FIRST, SECOND)).isEqualTo(write(FIRST, SECOND));
    }

    /** 30 万答卷的 Word 文档没人打得开：超出上限时作业失败，而不是产出一份打不开的文件。 */
    @Test
    void theNumberOfResponsesIsCapped() {
        assertThat(ExportFormat.DOCX.writer().maxDataRows()).isEqualTo(DocxExportWriter.MAX_RESPONSES);
    }

    /** 留一份样例在 target/ 下，便于用 Word / LibreOffice 人工核对中文与分页。 */
    @Test
    void aSampleDocumentIsLeftInTargetForExternalReaders() throws IOException {
        java.nio.file.Path sample = java.nio.file.Path.of("target", "export-sample.docx");
        java.nio.file.Files.createDirectories(sample.getParent());
        java.nio.file.Files.write(sample, write(FIRST, SECOND));

        assertThat(sample).exists();
    }
}
