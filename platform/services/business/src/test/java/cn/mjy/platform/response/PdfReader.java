package cn.mjy.platform.response;

import java.io.ByteArrayOutputStream;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.regex.Matcher;
import java.util.regex.Pattern;
import java.util.zip.DataFormatException;
import java.util.zip.Inflater;

/**
 * 测试用的最小 PDF 解析器，按 PDF 1.7 规范另写的一份，**不复用被测代码的任何常量或工具**
 * （与 {@link SavReader} 同一条规矩）。它做的事情：
 *
 * <ul>
 *   <li>从 {@code startxref} 找到交叉引用表，按表里的偏移取对象——顺带就把「xref 里的偏移是不是真的
 *       指向那个对象」验了，这是 PDF 最容易写错的地方；</li>
 *   <li>解开 {@code FlateDecode} 流；</li>
 *   <li>顺着页树取出每一页的内容流，按 {@code Tj} / {@code TJ} 抽出文本，
 *       再经字体的 {@code ToUnicode} CMap 把 2 字节编码还原成字符——
 *       <b>这条路径与真正的 PDF 阅读器取文本的路径相同</b>，中文能不能被提取出来靠它说话；</li>
 *   <li>取出字体描述符与嵌入的 {@code /FontFile2} 字节。</li>
 * </ul>
 */
final class PdfReader {

    /** 一页：文本片段（按出现顺序）。 */
    record Page(List<String> texts) {

        String text() {
            return String.join("", texts);
        }
    }

    /** 嵌入字体：描述符里的名字与字节，以及字体程序自己的表目录。 */
    record EmbeddedFont(String fontName, byte[] program, Map<String, byte[]> tables) {
    }

    private static final Pattern OBJECT_AT = Pattern.compile("\\G(\\d+)\\s+(\\d+)\\s+obj");
    private static final Pattern BF_CHAR = Pattern.compile("<([0-9A-Fa-f]+)>\\s*<([0-9A-Fa-f]+)>");

    private final byte[] bytes;
    private final String raw;
    /** 对象号 → 文件偏移，取自交叉引用表。 */
    private final Map<Integer, Integer> offsets = new LinkedHashMap<>();
    private final String trailer;

    private PdfReader(byte[] bytes) {
        this.bytes = bytes;
        this.raw = new String(bytes, StandardCharsets.ISO_8859_1);
        int startxref = raw.lastIndexOf("startxref");
        require(startxref >= 0, "没有 startxref");
        int xref = Integer.parseInt(raw.substring(startxref + "startxref".length()).trim().split("\\s+")[0]);
        this.trailer = readXref(xref);
    }

    static PdfReader of(byte[] pdf) {
        return new PdfReader(pdf);
    }

    String version() {
        return raw.substring(0, raw.indexOf('\n')).trim();
    }

    String trailer() {
        return trailer;
    }

    boolean endsProperly() {
        return raw.stripTrailing().endsWith("%%EOF");
    }

    /** 交叉引用表：{@code xref} / 段首「起始号 个数」/ 每条 20 字节；返回 trailer 字典原文。 */
    private String readXref(int at) {
        String tail = raw.substring(at);
        require(tail.startsWith("xref"), "交叉引用表不在 startxref 指的地方");
        int cursor = at + "xref".length();
        while (true) {
            Matcher section = Pattern.compile("\\G\\s*(\\d+)\\s+(\\d+)\\s*").matcher(raw);
            section.region(cursor, raw.length());
            if (!section.find()) {
                break;
            }
            int first = Integer.parseInt(section.group(1));
            int count = Integer.parseInt(section.group(2));
            cursor = section.end();
            for (int i = 0; i < count; i++) {
                String entry = raw.substring(cursor, cursor + 20);
                require(entry.length() == 20, "交叉引用项不是 20 字节");
                if (entry.charAt(17) == 'n') {
                    offsets.put(first + i, Integer.parseInt(entry.substring(0, 10)));
                }
                cursor += 20;
            }
            if (raw.startsWith("trailer", cursor) || raw.regionMatches(cursor, "\ntrailer", 0, 8)) {
                break;
            }
        }
        int trailerAt = raw.indexOf("trailer", cursor);
        require(trailerAt >= 0, "没有 trailer");
        return dictionaryAt(raw.indexOf("<<", trailerAt));
    }

    /** 按对象号取出对象体（不含 {@code N 0 obj} 与 {@code endobj}）。 */
    String object(int number) {
        Integer at = offsets.get(number);
        require(at != null, "交叉引用表里没有对象 " + number);
        Matcher header = OBJECT_AT.matcher(raw);
        header.region(at, raw.length());
        require(header.find(), "对象 " + number + " 的偏移 " + at + " 指的不是对象头");
        require(Integer.parseInt(header.group(1)) == number,
                "偏移指向对象 " + header.group(1) + "，不是 " + number);
        int end = raw.indexOf("endobj", header.end());
        require(end >= 0, "对象 " + number + " 没有 endobj");
        return raw.substring(header.end(), end).trim();
    }

    /** 对象里那个流的内容（必要时解压）。 */
    byte[] stream(int number) {
        Integer at = offsets.get(number);
        require(at != null, "交叉引用表里没有对象 " + number);
        String body = object(number);
        int keyword = raw.indexOf("stream", at);
        require(keyword >= 0, "对象 " + number + " 里没有流");
        int from = keyword + "stream".length();
        from += raw.charAt(from) == '\r' ? 1 : 0;
        require(raw.charAt(from) == '\n', "stream 关键字后必须紧跟换行");
        from++;
        int length = Integer.parseInt(value(body, "/Length").trim());
        byte[] data = new byte[length];
        System.arraycopy(bytes, from, data, 0, length);
        return body.contains("/FlateDecode") ? inflate(data) : data;
    }

    /** 页树里的每一页，按 {@code /Kids} 的顺序。 */
    List<Page> pages() {
        String root = object(reference(trailer, "/Root"));
        String tree = object(reference(root, "/Pages"));
        List<Page> pages = new ArrayList<>();
        for (int kid : references(value(tree, "/Kids"))) {
            String page = object(kid);
            require(page.contains("/Type /Page"), "页树的孩子不是页");
            pages.add(new Page(texts(new String(stream(reference(page, "/Contents")),
                    StandardCharsets.ISO_8859_1), toUnicode())));
        }
        require(pages.size() == Integer.parseInt(value(tree, "/Count").trim()), "/Count 与孩子数不符");
        return pages;
    }

    /** 嵌入的字体程序；{@code /FontFile2} 不在就是没嵌字体（那就是这次要防的事故）。 */
    EmbeddedFont embeddedFont() {
        String descriptor = object(descriptorNumber());
        require(descriptor.contains("/FontFile2"), "字体描述符里没有 /FontFile2：字体没嵌进来");
        byte[] program = stream(reference(descriptor, "/FontFile2"));
        return new EmbeddedFont(value(descriptor, "/FontName").trim(), program, tablesOf(program));
    }

    String fontDescriptor() {
        return object(descriptorNumber());
    }

    /** 复合字体（Type0）字典。 */
    String type0Font() {
        return object(fontNumber());
    }

    String descendantFont() {
        List<Integer> descendants = references(value(type0Font(), "/DescendantFonts"));
        require(!descendants.isEmpty(), "Type0 字体没有 /DescendantFonts");
        return object(descendants.getFirst());
    }

    /** {@code CIDToGIDMap} 流：编码 → 字形号。 */
    int[] cidToGid() {
        byte[] map = stream(reference(descendantFont(), "/CIDToGIDMap"));
        int[] gids = new int[map.length / 2];
        for (int i = 0; i < gids.length; i++) {
            gids[i] = ((map[2 * i] & 0xFF) << 8) | (map[2 * i + 1] & 0xFF);
        }
        return gids;
    }

    /** {@code ToUnicode} CMap：编码 → 字符串。 */
    Map<Integer, String> toUnicode() {
        Map<Integer, String> unicode = new LinkedHashMap<>();
        String cmap = new String(stream(reference(type0Font(), "/ToUnicode")), StandardCharsets.ISO_8859_1);
        Matcher pair = BF_CHAR.matcher(cmap);
        while (pair.find()) {
            unicode.put(Integer.parseInt(pair.group(1), 16), utf16be(pair.group(2)));
        }
        require(cmap.contains("begincmap") && cmap.contains("endcmap"), "ToUnicode 不是一份 CMap");
        return unicode;
    }

    private int fontNumber() {
        String page = object(references(value(object(reference(object(reference(trailer, "/Root")), "/Pages")),
                "/Kids")).getFirst());
        return reference(value(value(page, "/Resources"), "/Font"), "/F1");
    }

    private int descriptorNumber() {
        return reference(descendantFont(), "/FontDescriptor");
    }

    // ---- 内容流里的文本 ----

    /** 按 {@code Tj} / {@code TJ} 抽出文本片段；只认十六进制串，这个写出器不写别的。 */
    private static List<String> texts(String content, Map<Integer, String> unicode) {
        List<String> texts = new ArrayList<>();
        Matcher show = Pattern.compile("<([0-9A-Fa-f]*)>\\s*(Tj|TJ)").matcher(content);
        while (show.find()) {
            texts.add(decode(show.group(1), unicode));
        }
        return texts;
    }

    private static String decode(String hex, Map<Integer, String> unicode) {
        StringBuilder text = new StringBuilder();
        for (int i = 0; i + 4 <= hex.length(); i += 4) {
            int code = Integer.parseInt(hex.substring(i, i + 4), 16);
            String mapped = unicode.get(code);
            require(mapped != null, "ToUnicode 里没有编码 " + code + "：这个字提取不出来");
            text.append(mapped);
        }
        return text.toString();
    }

    private static String utf16be(String hex) {
        byte[] units = new byte[hex.length() / 2];
        for (int i = 0; i < units.length; i++) {
            units[i] = (byte) Integer.parseInt(hex.substring(2 * i, 2 * i + 2), 16);
        }
        return new String(units, StandardCharsets.UTF_16BE);
    }

    // ---- 字典与引用 ----

    /** 从 {@code <<} 起配平括号，取出整个字典（含两侧 {@code << >>}）。 */
    private String dictionaryAt(int from) {
        require(from >= 0, "没有字典");
        int depth = 0;
        for (int i = from; i + 1 < raw.length(); i++) {
            if (raw.startsWith("<<", i)) {
                depth++;
                i++;
            } else if (raw.startsWith(">>", i)) {
                if (--depth == 0) {
                    return raw.substring(from, i + 2);
                }
                i++;
            }
        }
        throw new AssertionError("字典没有闭合");
    }

    /** 字典里某个键的值（到下一个键或字典结束为止）。名字对象（{@code /Xxx}）单独处理。 */
    static String value(String dictionary, String key) {
        int at = dictionary.indexOf(key);
        if (at < 0) {
            return "";
        }
        int from = at + key.length();
        while (from < dictionary.length() && Character.isWhitespace(dictionary.charAt(from))) {
            from++;
        }
        if (from < dictionary.length() && dictionary.charAt(from) == '/') {
            int end = from + 1;
            while (end < dictionary.length() && "/[]<>() \t\r\n".indexOf(dictionary.charAt(end)) < 0) {
                end++;
            }
            return dictionary.substring(from, end);
        }
        int depth = 0;
        for (int i = from; i < dictionary.length(); i++) {
            char c = dictionary.charAt(i);
            if (c == '[' || c == '<' && i + 1 < dictionary.length() && dictionary.charAt(i + 1) == '<') {
                depth++;
            } else if (c == ']' || c == '>' && i + 1 < dictionary.length() && dictionary.charAt(i + 1) == '>') {
                if (depth == 0) {
                    return dictionary.substring(from, i).trim();
                }
                depth--;
            } else if (c == '/' && depth == 0 && i > from) {
                return dictionary.substring(from, i).trim();
            }
        }
        return dictionary.substring(from).trim();
    }

    /** {@code /Key N 0 R} 里的 N。 */
    static int reference(String dictionary, String key) {
        String value = value(dictionary, key);
        Matcher ref = Pattern.compile("(\\d+)\\s+\\d+\\s+R").matcher(value);
        return ref.find() ? Integer.parseInt(ref.group(1)) : 0;
    }

    static List<Integer> references(String array) {
        List<Integer> numbers = new ArrayList<>();
        Matcher ref = Pattern.compile("(\\d+)\\s+\\d+\\s+R").matcher(array);
        while (ref.find()) {
            numbers.add(Integer.parseInt(ref.group(1)));
        }
        return numbers;
    }

    // ---- TrueType 表目录 ----

    /** 嵌入字体程序的表目录：表名 → 表内容。 */
    static Map<String, byte[]> tablesOf(byte[] program) {
        require(program.length > 12, "字体程序太短");
        int count = ((program[4] & 0xFF) << 8) | (program[5] & 0xFF);
        Map<String, byte[]> tables = new LinkedHashMap<>();
        for (int i = 0; i < count; i++) {
            int record = 12 + 16 * i;
            String tag = new String(program, record, 4, StandardCharsets.US_ASCII);
            int offset = int32(program, record + 8);
            int length = int32(program, record + 12);
            require(offset + length <= program.length, "表 " + tag + " 超出字体程序");
            byte[] table = new byte[length];
            System.arraycopy(program, offset, table, 0, length);
            tables.put(tag, table);
        }
        return tables;
    }

    private static int int32(byte[] data, int at) {
        return ((data[at] & 0xFF) << 24) | ((data[at + 1] & 0xFF) << 16)
                | ((data[at + 2] & 0xFF) << 8) | (data[at + 3] & 0xFF);
    }

    private static byte[] inflate(byte[] data) {
        Inflater inflater = new Inflater();
        inflater.setInput(data);
        ByteArrayOutputStream out = new ByteArrayOutputStream();
        byte[] buffer = new byte[8192];
        try {
            while (!inflater.finished()) {
                int read = inflater.inflate(buffer);
                if (read == 0 && inflater.needsInput()) {
                    break;
                }
                out.write(buffer, 0, read);
            }
        } catch (DataFormatException e) {
            throw new AssertionError("流不是合法的 Flate 数据", e);
        } finally {
            inflater.end();
        }
        return out.toByteArray();
    }

    private static void require(boolean condition, String message) {
        if (!condition) {
            throw new AssertionError(message);
        }
    }
}
