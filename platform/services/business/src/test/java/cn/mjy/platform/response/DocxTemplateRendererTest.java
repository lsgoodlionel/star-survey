package cn.mjy.platform.response;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import java.io.ByteArrayInputStream;
import java.io.ByteArrayOutputStream;
import java.nio.charset.StandardCharsets;
import java.util.LinkedHashMap;
import java.util.Map;
import java.util.zip.ZipEntry;
import java.util.zip.ZipInputStream;
import java.util.zip.ZipOutputStream;
import javax.xml.parsers.DocumentBuilderFactory;
import org.junit.jupiter.api.Test;

class DocxTemplateRendererTest {

    private static final String TYPES = """
            <?xml version="1.0" encoding="UTF-8"?>
            <Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
              <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
              <Default Extension="xml" ContentType="application/xml"/>
              <Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
            </Types>
            """;
    private static final String ROOT_RELS = """
            <?xml version="1.0" encoding="UTF-8"?>
            <Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
              <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>
            </Relationships>
            """;

    @Test
    void rendersOnlyTheAllowlistedVariablesAndKeepsAParseableDocx() throws Exception {
        byte[] template = docx(Map.of("word/document.xml", document(
                "{{survey.title}} / {{response.id}} / {{response.version}} / {{response.answers}}")));

        byte[] rendered = DocxTemplateRenderer.render(template, new DocxTemplateRenderer.Values(
                "客户<满意度>", "7", "2", "问题一：很好 & 稳定"));

        Map<String, byte[]> entries = unzip(rendered);
        assertThat(entries.keySet()).containsExactly("[Content_Types].xml", "_rels/.rels", "word/document.xml");
        var factory = DocumentBuilderFactory.newInstance();
        factory.setFeature("http://apache.org/xml/features/disallow-doctype-decl", true);
        var parsed = factory.newDocumentBuilder().parse(new ByteArrayInputStream(entries.get("word/document.xml")));
        assertThat(parsed.getDocumentElement().getTextContent())
                .isEqualTo("客户<满意度> / 7 / 2 / 问题一：很好 & 稳定");
    }

    @Test
    void unknownOrMalformedVariablesFailClosedWithoutLeakingTheirNamesToOutput() throws Exception {
        for (String value : new String[] {"{{tenant.secret}}", "{{response.id", "prefix {{response.unknown}} suffix"}) {
            byte[] template = docx(Map.of("word/document.xml", document(value)));
            assertThatThrownBy(() -> DocxTemplateRenderer.render(template,
                    new DocxTemplateRenderer.Values("问卷", "7", "1", "答卷")))
                    .isInstanceOf(DocxTemplateRenderer.TemplateException.class)
                    .hasMessageContaining("模板变量不受支持");
        }
    }

    @Test
    void rejectsMacrosExternalRelationshipsAndUnsafeEntryNames() throws Exception {
        Map<String, byte[]> macro = baseEntries(document("{{response.id}}"));
        macro.put("word/vbaProject.bin", new byte[] {1});
        assertRejected(macro, "宏");

        Map<String, byte[]> external = baseEntries(document("{{response.id}}"));
        external.put("word/_rels/document.xml.rels", """
                <Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
                  <Relationship Id="rId9" Type="image" Target="https://internal.example/secret" TargetMode="External"/>
                </Relationships>
                """.getBytes(StandardCharsets.UTF_8));
        assertRejected(external, "外部关系");

        Map<String, byte[]> encodedExternal = baseEntries(document("{{response.id}}"));
        encodedExternal.put("word/_rels/document.xml.rels", """
                <Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
                  <Relationship Id="rId9" Type="image" Target="https://internal.example/secret"
                                TargetMode="Extern&#x61;l"/>
                </Relationships>
                """.getBytes(StandardCharsets.UTF_8));
        assertRejected(encodedExternal, "外部关系");

        Map<String, byte[]> encodedMacro = baseEntries(document("{{response.id}}"));
        encodedMacro.put("word/_rels/document.xml.rels", """
                <Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
                  <Relationship Id="rId8" Type="http://schemas.microsoft.com/office/2006/relationships/vbaPro&#x6a;ect"
                                Target="payload.bin"/>
                </Relationships>
                """.getBytes(StandardCharsets.UTF_8));
        assertRejected(encodedMacro, "宏");

        Map<String, byte[]> traversal = baseEntries(document("{{response.id}}"));
        traversal.put("word/../secrets.xml", "secret".getBytes(StandardCharsets.UTF_8));
        assertRejected(traversal, "条目路径");
    }

    @Test
    void enforcesEntryAndExpandedSizeLimitsBeforeRendering() throws Exception {
        Map<String, byte[]> tooMany = baseEntries(document("{{response.id}}"));
        for (int i = 0; i < DocxTemplateRenderer.MAX_ENTRIES; i++) {
            tooMany.put("word/media/image-" + i + ".png", new byte[] {1});
        }
        assertRejected(tooMany, "条目数量");

        Map<String, byte[]> expanded = baseEntries(document("{{response.id}}"));
        expanded.put("word/media/large.bin", new byte[DocxTemplateRenderer.MAX_ENTRY_BYTES + 1]);
        assertRejected(expanded, "条目过大");
    }

    @Test
    void uploadValidationAlsoEnforcesTheCompressedSizeLimit() {
        byte[] oversized = new byte[DocxTemplateRenderer.MAX_COMPRESSED_BYTES + 1];

        assertThatThrownBy(() -> DocxTemplateRenderer.validate(oversized))
                .isInstanceOf(DocxTemplateRenderer.TemplateException.class)
                .hasMessageContaining("压缩包大小");
    }

    @Test
    void renderingIsByteDeterministic() throws Exception {
        byte[] template = docx(Map.of("word/document.xml", document("{{response.id}}")));
        DocxTemplateRenderer.Values values = new DocxTemplateRenderer.Values("问卷", "7", "1", "答案");
        assertThat(DocxTemplateRenderer.render(template, values))
                .isEqualTo(DocxTemplateRenderer.render(template, values));
    }

    private static void assertRejected(Map<String, byte[]> entries, String message) throws Exception {
        byte[] template = zip(entries);
        assertThatThrownBy(() -> DocxTemplateRenderer.render(template,
                new DocxTemplateRenderer.Values("问卷", "7", "1", "答卷")))
                .isInstanceOf(DocxTemplateRenderer.TemplateException.class)
                .hasMessageContaining(message);
    }

    private static String document(String text) {
        return "<?xml version=\"1.0\" encoding=\"UTF-8\"?>"
                + "<w:document xmlns:w=\"http://schemas.openxmlformats.org/wordprocessingml/2006/main\">"
                + "<w:body><w:p><w:r><w:t>" + text + "</w:t></w:r></w:p></w:body></w:document>";
    }

    private static byte[] docx(Map<String, String> extra) throws Exception {
        Map<String, byte[]> entries = baseEntries(extra.get("word/document.xml"));
        for (Map.Entry<String, String> item : extra.entrySet()) {
            entries.put(item.getKey(), item.getValue().getBytes(StandardCharsets.UTF_8));
        }
        return zip(entries);
    }

    private static Map<String, byte[]> baseEntries(String document) {
        Map<String, byte[]> entries = new LinkedHashMap<>();
        entries.put("[Content_Types].xml", TYPES.getBytes(StandardCharsets.UTF_8));
        entries.put("_rels/.rels", ROOT_RELS.getBytes(StandardCharsets.UTF_8));
        entries.put("word/document.xml", document.getBytes(StandardCharsets.UTF_8));
        return entries;
    }

    private static byte[] zip(Map<String, byte[]> entries) throws Exception {
        ByteArrayOutputStream output = new ByteArrayOutputStream();
        try (ZipOutputStream zip = new ZipOutputStream(output)) {
            for (Map.Entry<String, byte[]> item : entries.entrySet()) {
                zip.putNextEntry(new ZipEntry(item.getKey()));
                zip.write(item.getValue());
                zip.closeEntry();
            }
        }
        return output.toByteArray();
    }

    private static Map<String, byte[]> unzip(byte[] value) throws Exception {
        Map<String, byte[]> entries = new LinkedHashMap<>();
        try (ZipInputStream zip = new ZipInputStream(new ByteArrayInputStream(value))) {
            ZipEntry entry;
            while ((entry = zip.getNextEntry()) != null) {
                entries.put(entry.getName(), zip.readAllBytes());
            }
        }
        return entries;
    }
}
