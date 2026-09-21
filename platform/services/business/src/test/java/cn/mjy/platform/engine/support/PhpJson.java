package cn.mjy.platform.engine.support;

import java.util.List;
import java.util.Map;

/**
 * 按 PHP {@code json_encode($value, JSON_UNESCAPED_UNICODE)} 的输出规则逐字节重现 JSON，
 * 用于契约测试：平台必须接受 PHP 发送端真实产生的字节，而不是 Java 序列化器"等价"的字节。
 *
 * 重现的要点（均为 PHP 7.1+ 默认行为）：
 * <ul>
 *   <li>无任何空白；对象键按插入顺序输出；</li>
 *   <li>{@code /} 转义为 {@code \/}（未加 JSON_UNESCAPED_SLASHES）；</li>
 *   <li>非 ASCII 字符原样输出为 UTF-8（JSON_UNESCAPED_UNICODE）；</li>
 *   <li>{@code \b \f \n \r \t} 用短转义，其余控制字符用小写十六进制 {@code \\}{@code u00xx}；</li>
 *   <li>整数输出为数字，null/true/false 为字面量。</li>
 * </ul>
 * 只支持事件信封用到的类型：有序 Map（对应 PHP 关联数组）、List（对应索引数组）、String、整数、null。
 */
public final class PhpJson {

    private PhpJson() {
    }

    public static String encode(Object value) {
        StringBuilder out = new StringBuilder();
        write(out, value);
        return out.toString();
    }

    private static void write(StringBuilder out, Object value) {
        switch (value) {
            case null -> out.append("null");
            case String text -> writeString(out, text);
            case Integer number -> out.append(number);
            case Long number -> out.append(number);
            case Boolean flag -> out.append(flag);
            case Map<?, ?> map -> writeObject(out, map);
            case List<?> list -> writeArray(out, list);
            default -> throw new IllegalArgumentException("unsupported type: " + value.getClass());
        }
    }

    private static void writeObject(StringBuilder out, Map<?, ?> map) {
        out.append('{');
        boolean first = true;
        for (Map.Entry<?, ?> entry : map.entrySet()) {
            if (!first) {
                out.append(',');
            }
            first = false;
            writeString(out, entry.getKey().toString());
            out.append(':');
            write(out, entry.getValue());
        }
        out.append('}');
    }

    private static void writeArray(StringBuilder out, List<?> list) {
        out.append('[');
        for (int i = 0; i < list.size(); i++) {
            if (i > 0) {
                out.append(',');
            }
            write(out, list.get(i));
        }
        out.append(']');
    }

    private static void writeString(StringBuilder out, String text) {
        out.append('"');
        text.codePoints().forEach(codePoint -> appendCodePoint(out, codePoint));
        out.append('"');
    }

    private static void appendCodePoint(StringBuilder out, int codePoint) {
        switch (codePoint) {
            case '"' -> out.append("\\\"");
            case '\\' -> out.append("\\\\");
            case '/' -> out.append("\\/");
            case '\b' -> out.append("\\b");
            case '\f' -> out.append("\\f");
            case '\n' -> out.append("\\n");
            case '\r' -> out.append("\\r");
            case '\t' -> out.append("\\t");
            default -> {
                if (codePoint < 0x20) {
                    out.append(String.format("\\u%04x", codePoint));
                } else {
                    out.appendCodePoint(codePoint);
                }
            }
        }
    }
}
