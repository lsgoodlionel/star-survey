package cn.mjy.platform.response;

/**
 * OOXML 文本的统一出口：XML 转义，并丢弃 XML 1.0 不允许的字符（控制字符、孤立代理、U+FFFE/U+FFFF）。
 * XLSX 与 DOCX 共用——同一份作答写进两种包必须得到同样的文本，两处各写一遍迟早会分叉。
 *
 * <p>防公式注入是另一件事，在更外层的 {@link ExportCellGuard} 做。
 */
final class ExportXml {

    private ExportXml() {
    }

    static String escape(String value) {
        StringBuilder out = new StringBuilder(value.length() + 16);
        for (int i = 0; i < value.length(); ) {
            int cp = value.codePointAt(i);
            i += Character.charCount(cp);
            if (!allowedInXml(cp)) {
                continue;
            }
            switch (cp) {
                case '<' -> out.append("&lt;");
                case '>' -> out.append("&gt;");
                case '&' -> out.append("&amp;");
                case '"' -> out.append("&quot;");
                case '\r' -> out.append("&#13;");
                default -> out.appendCodePoint(cp);
            }
        }
        return out.toString();
    }

    private static boolean allowedInXml(int cp) {
        return cp == 0x9 || cp == 0xA || cp == 0xD
                || (cp >= 0x20 && cp <= 0xD7FF)
                || (cp >= 0xE000 && cp <= 0xFFFD)
                || (cp >= 0x10000 && cp <= 0x10FFFF);
    }
}
