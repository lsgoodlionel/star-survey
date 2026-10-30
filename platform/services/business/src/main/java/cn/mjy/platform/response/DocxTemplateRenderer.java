package cn.mjy.platform.response;

import java.io.ByteArrayInputStream;
import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.io.OutputStream;
import java.nio.charset.StandardCharsets;
import java.util.LinkedHashMap;
import java.util.Map;
import java.util.Objects;
import java.util.Set;
import java.util.regex.Matcher;
import java.util.regex.Pattern;
import java.util.zip.ZipEntry;
import java.util.zip.ZipInputStream;
import javax.xml.XMLConstants;
import javax.xml.parsers.DocumentBuilderFactory;
import org.w3c.dom.Element;
import org.xml.sax.SAXException;

/**
 * 自定义 Word 模板的无执行渲染核心。
 *
 * <p>模板只是一个受限 DOCX 包：不执行宏，不加载外部关系，不解释任意表达式。变量必须完整地位于
 * 一个 XML 文本片段中，并且只能来自固定白名单。这样模板内容不会变成读取租户、用户或运行环境秘密的
 * 查询语言。上传接口应在保存版本前调用本类；导出作业应冻结模板版本和字节摘要后再调用渲染。
 */
final class DocxTemplateRenderer {

    static final int MAX_COMPRESSED_BYTES = 4 * 1024 * 1024;
    static final int MAX_ENTRY_BYTES = 8 * 1024 * 1024;
    static final int MAX_EXPANDED_BYTES = 16 * 1024 * 1024;
    static final int MAX_ENTRIES = 128;

    private static final Set<String> REQUIRED = Set.of(
            "[Content_Types].xml", "_rels/.rels", "word/document.xml");
    private static final Pattern VARIABLE = Pattern.compile("\\{\\{([a-z][a-z0-9.]*)}}", Pattern.CASE_INSENSITIVE);

    private DocxTemplateRenderer() {
    }

    record Values(String surveyTitle, String responseId, String responseVersion, String responseAnswers) {
        Values {
            Objects.requireNonNull(surveyTitle, "surveyTitle");
            Objects.requireNonNull(responseId, "responseId");
            Objects.requireNonNull(responseVersion, "responseVersion");
            Objects.requireNonNull(responseAnswers, "responseAnswers");
        }

        Map<String, String> variables() {
            return Map.of(
                    "survey.title", surveyTitle,
                    "response.id", responseId,
                    "response.version", responseVersion,
                    "response.answers", responseAnswers);
        }
    }

    /** 上传时只做安全与变量白名单验证，不保存一份已经替换过变量的内容。 */
    static void validate(byte[] template) throws IOException {
        Map<String, byte[]> parts = readAndValidate(template);
        Map<String, String> placeholders = new Values("", "", "", "").variables();
        for (Map.Entry<String, byte[]> part : parts.entrySet()) {
            if (isXml(part.getKey())) {
                renderXml(part.getValue(), placeholders);
            }
        }
    }

    static byte[] render(byte[] template, Values values) throws IOException {
        Objects.requireNonNull(template, "template");
        Objects.requireNonNull(values, "values");

        Map<String, byte[]> parts = readAndValidate(template);
        ByteArrayOutputStream output = new ByteArrayOutputStream(Math.min(template.length * 2, MAX_EXPANDED_BYTES));
        try (ExportZip zip = new ExportZip(output)) {
            for (Map.Entry<String, byte[]> part : parts.entrySet()) {
                byte[] content = part.getValue();
                if (isXml(part.getKey())) {
                    content = renderXml(content, values.variables());
                }
                try (OutputStream entry = zip.entry(part.getKey())) {
                    entry.write(content);
                }
            }
        }
        return output.toByteArray();
    }

    private static Map<String, byte[]> readAndValidate(byte[] template) throws IOException {
        Objects.requireNonNull(template, "template");
        if (template.length == 0 || template.length > MAX_COMPRESSED_BYTES) {
            throw new TemplateException("模板压缩包大小超出限制");
        }
        Map<String, byte[]> parts = new LinkedHashMap<>();
        long total = 0;
        try (ZipInputStream zip = new ZipInputStream(new ByteArrayInputStream(template))) {
            ZipEntry entry;
            while ((entry = zip.getNextEntry()) != null) {
                if (entry.isDirectory()) {
                    throw new TemplateException("模板包含不允许的目录条目");
                }
                String name = entry.getName();
                validateName(name);
                if (parts.size() >= MAX_ENTRIES) {
                    throw new TemplateException("模板条目数量超出限制");
                }
                if (parts.containsKey(name)) {
                    throw new TemplateException("模板包含重复条目");
                }
                byte[] content = readEntry(zip);
                total += content.length;
                if (total > MAX_EXPANDED_BYTES) {
                    throw new TemplateException("模板解压后大小超出限制");
                }
                validatePart(name, content);
                parts.put(name, content);
            }
        } catch (TemplateException error) {
            throw error;
        } catch (IOException error) {
            throw new TemplateException("模板不是有效的 DOCX 压缩包", error);
        }
        if (!parts.keySet().containsAll(REQUIRED)) {
            throw new TemplateException("模板缺少 DOCX 必需条目");
        }
        return parts;
    }

    private static byte[] readEntry(ZipInputStream zip) throws IOException {
        ByteArrayOutputStream output = new ByteArrayOutputStream();
        byte[] buffer = new byte[8192];
        int count;
        while ((count = zip.read(buffer)) != -1) {
            if (output.size() + count > MAX_ENTRY_BYTES) {
                throw new TemplateException("模板条目过大");
            }
            output.write(buffer, 0, count);
        }
        return output.toByteArray();
    }

    private static void validateName(String name) throws TemplateException {
        if (name == null || name.isBlank() || name.startsWith("/") || name.contains("\\")
                || name.contains(":") || name.indexOf('\0') >= 0) {
            throw new TemplateException("模板条目路径不安全");
        }
        for (String component : name.split("/", -1)) {
            if (component.isEmpty() || component.equals(".") || component.equals("..")) {
                throw new TemplateException("模板条目路径不安全");
            }
        }
    }

    private static void validatePart(String name, byte[] content) throws TemplateException {
        String lowered = name.toLowerCase(java.util.Locale.ROOT);
        if (lowered.endsWith("vbaproject.bin") || lowered.endsWith(".docm") || lowered.contains("/activex/")) {
            throw new TemplateException("模板不得包含宏或活动内容");
        }
        if (name.equals("[Content_Types].xml") || lowered.endsWith(".rels")) {
            validateXmlSecurity(name, content);
        }
    }

    private static void validateXmlSecurity(String name, byte[] content) throws TemplateException {
        try {
            DocumentBuilderFactory factory = DocumentBuilderFactory.newInstance();
            factory.setNamespaceAware(true);
            factory.setXIncludeAware(false);
            factory.setExpandEntityReferences(false);
            factory.setFeature("http://apache.org/xml/features/disallow-doctype-decl", true);
            factory.setFeature("http://xml.org/sax/features/external-general-entities", false);
            factory.setFeature("http://xml.org/sax/features/external-parameter-entities", false);
            factory.setFeature("http://apache.org/xml/features/nonvalidating/load-external-dtd", false);
            factory.setAttribute(XMLConstants.ACCESS_EXTERNAL_DTD, "");
            factory.setAttribute(XMLConstants.ACCESS_EXTERNAL_SCHEMA, "");
            var document = factory.newDocumentBuilder().parse(new ByteArrayInputStream(content));
            var elements = document.getElementsByTagName("*");
            for (int i = 0; i < elements.getLength(); i++) {
                Element element = (Element) elements.item(i);
                String targetMode = element.getAttribute("TargetMode");
                if ("external".equalsIgnoreCase(targetMode)) {
                    throw new TemplateException("模板不得包含外部关系");
                }
                var attributes = element.getAttributes();
                for (int j = 0; j < attributes.getLength(); j++) {
                    String value = attributes.item(j).getNodeValue().toLowerCase(java.util.Locale.ROOT);
                    if (value.contains("macroenabled") || value.contains("vbaproject")
                            || value.contains("/activex") || value.contains("activex")) {
                        throw new TemplateException("模板不得包含宏或活动内容");
                    }
                }
            }
        } catch (TemplateException error) {
            throw error;
        } catch (SAXException | RuntimeException | javax.xml.parsers.ParserConfigurationException | IOException error) {
            throw new TemplateException("模板包含不可安全解析的 XML：" + name, error);
        }
    }

    private static boolean isXml(String name) {
        String lowered = name.toLowerCase(java.util.Locale.ROOT);
        return lowered.endsWith(".xml") || lowered.endsWith(".rels");
    }

    private static byte[] renderXml(byte[] content, Map<String, String> variables) throws TemplateException {
        String xml = new String(content, StandardCharsets.UTF_8);
        Matcher matcher = VARIABLE.matcher(xml);
        StringBuffer validation = new StringBuffer();
        while (matcher.find()) {
            if (!variables.containsKey(matcher.group(1))) {
                throw new TemplateException("模板变量不受支持");
            }
            matcher.appendReplacement(validation, "");
        }
        matcher.appendTail(validation);
        if (validation.indexOf("{{") >= 0 || validation.indexOf("}}") >= 0) {
            throw new TemplateException("模板变量不受支持");
        }

        matcher.reset();
        StringBuffer rendered = new StringBuffer();
        while (matcher.find()) {
            String escaped = ExportXml.escape(variables.get(matcher.group(1)));
            matcher.appendReplacement(rendered, Matcher.quoteReplacement(escaped));
        }
        matcher.appendTail(rendered);
        return rendered.toString().getBytes(StandardCharsets.UTF_8);
    }

    static final class TemplateException extends IOException {
        TemplateException(String message) {
            super(message);
        }

        TemplateException(String message, Throwable cause) {
            super(message, cause);
        }
    }
}
