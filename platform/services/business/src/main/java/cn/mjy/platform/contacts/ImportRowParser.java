package cn.mjy.platform.contacts;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import org.springframework.stereotype.Component;
import tools.jackson.core.JacksonException;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.json.JsonMapper;

/**
 * 把 CSV / JSON 原文切成"一行一个 JSON 对象"。这里只做结构解析，<b>不做</b>字段校验——
 * 校验在后台逐行进行，坏行只记结果不中断作业（R18-01"错误行反馈准确"）。
 */
@Component
class ImportRowParser {

    static final String CSV = "csv";
    static final String JSON = "json";

    /** 认得的列名；其余列忽略。 */
    private static final List<String> COLUMNS = List.of(
            "name", "email", "phone", "employee_id", "provider", "app_id", "external_id", "org_unit");

    private final JsonMapper json;

    ImportRowParser(JsonMapper json) {
        this.json = json;
    }

    List<String> parse(String format, String content) {
        if (content == null || content.isBlank()) {
            throw new InvalidContactRequestException("content is required");
        }
        return switch (format == null ? "" : format) {
            case CSV -> parseCsv(content);
            case JSON -> parseJson(content);
            default -> throw new InvalidContactRequestException("format must be csv or json");
        };
    }

    private List<String> parseCsv(String content) {
        List<String> lines = content.lines().filter(line -> !line.isBlank()).toList();
        if (lines.isEmpty()) {
            throw new InvalidContactRequestException("csv must have a header row");
        }
        List<String> header = splitCsv(lines.getFirst()).stream().map(h -> h.trim().toLowerCase(java.util.Locale.ROOT))
                .toList();
        if (header.stream().noneMatch(COLUMNS::contains)) {
            throw new InvalidContactRequestException("csv header has none of the known columns " + COLUMNS);
        }
        List<String> rows = new ArrayList<>(lines.size() - 1);
        for (String line : lines.subList(1, lines.size())) {
            List<String> cells = splitCsv(line);
            Map<String, String> row = new LinkedHashMap<>();
            for (int i = 0; i < header.size() && i < cells.size(); i++) {
                if (COLUMNS.contains(header.get(i))) {
                    row.put(header.get(i), cells.get(i).trim());
                }
            }
            rows.add(json.writeValueAsString(row));
        }
        return rows;
    }

    private List<String> parseJson(String content) {
        JsonNode array;
        try {
            array = json.readTree(content);
        } catch (JacksonException e) {
            throw new InvalidContactRequestException("content is not valid json");
        }
        if (!array.isArray()) {
            throw new InvalidContactRequestException("json import must be an array of objects");
        }
        List<String> rows = new ArrayList<>(array.size());
        for (JsonNode node : array) {
            Map<String, String> row = new LinkedHashMap<>();
            if (node.isObject()) {
                for (String column : COLUMNS) {
                    String value = text(node.get(column));
                    if (value != null) {
                        row.put(column, value);
                    }
                }
            }
            rows.add(json.writeValueAsString(row));
        }
        return rows;
    }

    /** 逐行读回时用：把 JSON 对象变回列名 → 值。 */
    Map<String, String> toColumns(String payload) {
        JsonNode node = json.readTree(payload);
        Map<String, String> row = new LinkedHashMap<>();
        for (String column : COLUMNS) {
            String value = text(node.get(column));
            if (value != null) {
                row.put(column, value);
            }
        }
        return row;
    }

    /** 取出一个非空白的字符串值；不是字符串的按原样序列化，交给逐行校验去拒绝。 */
    private static String text(JsonNode value) {
        if (value == null || value.isNull()) {
            return null;
        }
        String raw = (value.isString() ? value.asString() : value.toString()).trim();
        return raw.isEmpty() ? null : raw;
    }

    /** 极简 CSV：逗号分隔，支持双引号包裹与成对双引号转义。 */
    private static List<String> splitCsv(String line) {
        List<String> cells = new ArrayList<>();
        StringBuilder cell = new StringBuilder();
        boolean quoted = false;
        for (int i = 0; i < line.length(); i++) {
            char c = line.charAt(i);
            if (quoted && c == '"' && i + 1 < line.length() && line.charAt(i + 1) == '"') {
                cell.append('"');
                i++;
            } else if (c == '"') {
                quoted = !quoted;
            } else if (c == ',' && !quoted) {
                cells.add(cell.toString());
                cell.setLength(0);
            } else {
                cell.append(c);
            }
        }
        cells.add(cell.toString());
        return cells;
    }
}
