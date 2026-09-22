package cn.mjy.platform.response;

import java.util.ArrayList;
import java.util.List;
import java.util.Locale;
import java.util.regex.Pattern;
import tools.jackson.core.JacksonException;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.json.JsonMapper;

/**
 * 文件上传题（题型 {@code |}）作答的附件清单（ADR 0015 决定 6）。引擎把上传的文件写成 JSON 数组
 * {@code [{"title","comment","size","name","filename","ext"}]}；这里只做解析，<b>从不访问任何地址</b>——
 * 值里即使出现 URL（包括内网、云元数据地址）也只当作文本。
 *
 * <p>校验和：值里自带合法的 {@code sha256}（64 位十六进制）才填，否则留空；平台不为算哈希去下载文件。
 * 解析不了的值（不是 JSON 数组、已遮蔽）得到空清单。
 */
final class AttachmentManifest {

    static final String UPLOAD_TYPE = "|";
    private static final Pattern SHA256 = Pattern.compile("[0-9a-f]{64}");
    private static final int MAX_FILES_PER_ANSWER = 1000;
    private static final int MAX_TEXT = 1024;
    private static final JsonMapper JSON = JsonMapper.builder().build();

    /** 清单里的一个文件；全部是文本，空串表示未知。 */
    record Attachment(String name, String size, String ext, String storedName, String sha256) {
    }

    private AttachmentManifest() {
    }

    static List<Attachment> parse(String value) {
        if (value == null || value.isBlank()) {
            return List.of();
        }
        JsonNode files;
        try {
            files = JSON.readTree(value);
        } catch (JacksonException e) {
            return List.of();
        }
        if (files == null || !files.isArray()) {
            return List.of();
        }
        List<Attachment> result = new ArrayList<>();
        for (JsonNode file : files) {
            if (result.size() >= MAX_FILES_PER_ANSWER) {
                break;
            }
            if (file.isObject()) {
                result.add(attachment(file));
            }
        }
        return List.copyOf(result);
    }

    private static Attachment attachment(JsonNode file) {
        String sha = text(file.get("sha256")).toLowerCase(Locale.ROOT);
        return new Attachment(text(file.get("name")), text(file.get("size")), text(file.get("ext")),
                text(file.get("filename")), SHA256.matcher(sha).matches() ? sha : "");
    }

    private static String text(JsonNode node) {
        if (node == null || node.isNull() || node.isContainer()) {
            return "";
        }
        String text = node.isString() ? node.asString() : node.toString();
        return text.length() > MAX_TEXT ? text.substring(0, MAX_TEXT) : text;
    }
}
