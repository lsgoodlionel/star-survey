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
import org.w3c.dom.NodeList;

/** 纯单元：XLSX 的 ZIP 结构与每个 XML 部件合法、CSV 转义与防公式注入、附件清单只解析不抓取。 */
class ExportFormatTest {

    private static final List<List<String>> ROWS = List.of(
            Arrays.asList("version", "QTEXT", "QNUM"),
            Arrays.asList("版本", "短文本 <b>&\"'", "数值"),
            Arrays.asList("1", "=cmd|' /C calc'!A0", "-12.5"),
            Arrays.asList("1", null, "+86 138"),
            Arrays.asList("2", "line1\nline2, \"quoted\"", "@SUM(A1)"),
            Arrays.asList("2", "badcontrol￾chars", "\ttab"));

    private static ExportSheet sheet(String file, String title, List<List<String>> rows) {
        return ExportSheet.of(file, title, 2, sink -> {
            for (List<String> row : rows) {
                sink.accept(row);
            }
        });
    }

    private static List<ExportSheet> workbook() {
        return List.of(sheet("responses", "答卷", ROWS),
                sheet("fields", "字段字典", List.of(List.of("column", "label"), List.of("QTEXT", "短文本"))),
                sheet("attachments", "附件清单", List.of(List.of("responseid", "name"))));
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

    private static byte[] write(ExportFormat format) throws IOException {
        ByteArrayOutputStream out = new ByteArrayOutputStream();
        format.writer().write(workbook(), out);
        return out.toByteArray();
    }

    @Test
    void theXlsxIsAZipOfWellFormedPartsWithEveryRequiredPart() throws Exception {
        Map<String, byte[]> parts = unzip(write(ExportFormat.XLSX));

        assertThat(parts.keySet()).containsExactly("[Content_Types].xml", "_rels/.rels", "docProps/app.xml",
                "xl/workbook.xml", "xl/_rels/workbook.xml.rels", "xl/styles.xml",
                "xl/worksheets/sheet1.xml", "xl/worksheets/sheet2.xml", "xl/worksheets/sheet3.xml");
        for (Map.Entry<String, byte[]> part : parts.entrySet()) {
            parse(part.getValue());
        }
        Document workbook = parse(parts.get("xl/workbook.xml"));
        NodeList sheets = workbook.getElementsByTagNameNS("*", "sheet");
        assertThat(sheets.getLength()).isEqualTo(3);
        assertThat(sheets.item(0).getAttributes().getNamedItem("name").getNodeValue()).isEqualTo("答卷");
        String types = new String(parts.get("[Content_Types].xml"), StandardCharsets.UTF_8);
        assertThat(types).contains("/xl/worksheets/sheet3.xml", "/xl/workbook.xml", "/xl/styles.xml");
    }

    @Test
    void xlsxCellsAreInlineStringsNeverFormulasAndControlCharsAreDropped() throws Exception {
        Document sheet = parse(unzip(write(ExportFormat.XLSX)).get("xl/worksheets/sheet1.xml"));

        NodeList rows = sheet.getElementsByTagNameNS("*", "row");
        NodeList cells = sheet.getElementsByTagNameNS("*", "c");
        List<String> texts = new ArrayList<>();
        NodeList t = sheet.getElementsByTagNameNS("*", "t");
        for (int i = 0; i < t.getLength(); i++) {
            texts.add(t.item(i).getTextContent());
        }
        assertThat(rows.getLength()).isEqualTo(ROWS.size());
        assertThat(sheet.getElementsByTagNameNS("*", "f").getLength()).isZero();
        for (int i = 0; i < cells.getLength(); i++) {
            var type = cells.item(i).getAttributes().getNamedItem("t");
            assertThat(type == null ? cells.item(i).hasChildNodes() : !"inlineStr".equals(type.getNodeValue()))
                    .as("every non-empty cell is an inline string").isFalse();
        }
        assertThat(texts).contains("短文本 <b>&\"'", "=cmd|' /C calc'!A0", "line1\nline2, \"quoted\"",
                "badcontrolchars");
    }

    /** 留一份样例在 target/ 下，便于用 Excel / LibreOffice / openpyxl 人工或脚本核对。 */
    @Test
    void aSampleWorkbookIsLeftInTargetForExternalReaders() throws IOException {
        java.nio.file.Path sample = java.nio.file.Path.of("target", "export-sample.xlsx");
        java.nio.file.Files.createDirectories(sample.getParent());
        java.nio.file.Files.write(sample, write(ExportFormat.XLSX));

        assertThat(sample).exists();
    }

    @Test
    void theSameInputProducesByteIdenticalFiles() throws IOException {
        assertThat(write(ExportFormat.XLSX)).isEqualTo(write(ExportFormat.XLSX));
        assertThat(write(ExportFormat.CSV)).isEqualTo(write(ExportFormat.CSV));
    }

    @Test
    void theCsvBundleHasBomCrlfQuotingAndFormulaGuards() throws IOException {
        Map<String, byte[]> files = unzip(write(ExportFormat.CSV));

        assertThat(files.keySet()).containsExactly("responses.csv", "fields.csv", "attachments.csv");
        byte[] responses = files.get("responses.csv");
        assertThat(responses).startsWith(0xEF, 0xBB, 0xBF);
        String text = new String(responses, 3, responses.length - 3, StandardCharsets.UTF_8);
        assertThat(text).contains("\r\n");
        List<List<String>> rows = ExportFixture.parseCsv(text);
        assertThat(rows.get(2)).containsExactly("1", "'=cmd|' /C calc'!A0", "-12.5");
        assertThat(rows.get(3)).containsExactly("1", "", "'+86 138");
        assertThat(rows.get(4)).containsExactly("2", "line1\nline2, \"quoted\"", "'@SUM(A1)");
        assertThat(rows.get(5).get(2)).isEqualTo("'\ttab");
    }

    @Test
    void theAttachmentManifestParsesUploadJsonWithoutTouchingAnyUrl() {
        String value = "[{\"title\":\"x\",\"comment\":\"\",\"size\":\"12.5\",\"name\":\"证件.png\","
                + "\"filename\":\"fu_abc123\",\"ext\":\"png\",\"sha256\":\"" + "a".repeat(64) + "\"},"
                + "{\"name\":\"http://10.0.0.1/admin\",\"size\":\"1\",\"filename\":\"fu_def\",\"ext\":\"jpg\","
                + "\"url\":\"http://169.254.169.254/latest/meta-data\"}]";

        List<AttachmentManifest.Attachment> files = AttachmentManifest.parse(value);

        assertThat(files).hasSize(2);
        assertThat(files.get(0)).isEqualTo(new AttachmentManifest.Attachment("证件.png", "12.5", "png", "fu_abc123",
                "a".repeat(64)));
        assertThat(files.get(1).name()).isEqualTo("http://10.0.0.1/admin");
        assertThat(files.get(1).sha256()).isEmpty();
        assertThat(AttachmentManifest.parse("not json")).isEmpty();
        assertThat(AttachmentManifest.parse(null)).isEmpty();
        assertThat(AttachmentManifest.parse("[{\"sha256\":\"not-a-hash\",\"name\":\"n\"}]").get(0).sha256()).isEmpty();
    }
}
