package cn.mjy.platform.response;

import java.nio.charset.StandardCharsets;
import java.util.Locale;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.util.LinkedHashMap;
import java.util.Map;
import java.util.Set;
import java.util.TreeSet;

/**
 * 一份 PDF 用到的字体编码。它是这一份文档的账本：见过哪些字符、各自分到哪个编码、
 * 要嵌哪些字形，以及由此生成 {@code /W}、{@code CIDToGIDMap}、{@code ToUnicode} 三样。
 *
 * <p><b>编码不等于字形号。</b>这里给每个见过的字符按出现顺序分一个 2 字节编码，
 * 再用 {@code CIDToGIDMap} 流把编码指到字形。多这一层的好处正是子集外字符的出路：
 * 假名、谚文、emoji、生僻字都指向替代字形 {@code 〓}，但各自的 {@code ToUnicode}
 * 仍然是<b>它本来的码位</b>——页面上看到的是替代符，复制、检索、第三方读回拿到的是原文。
 * 宁愿这样也不整份作业失败：作答内容是作答者给的，不能因为一个 emoji 就导不出来。
 *
 * <p>内存有界（ADR 0015 硬约束）：账本的大小只与<b>不同字符数</b>有关，与答卷份数无关。
 */
final class PdfFontUse {

    /** Identity-H 是 2 字节编码，0 留给「没映射」，所以编码从 1 开始、到 0xFFFF 为止。 */
    private static final int FIRST_CODE = 1;
    private static final int LAST_CODE = 0xFFFF;
    /** 子集标记：六个大写字母，按惯例写在 {@code /BaseFont} 前面，表示这不是完整字体。 */
    private static final int TAG_LETTERS = 6;

    private final PdfFontProgram program;
    /** 码位 → 编码，按首次出现的顺序分配（所以同样的输入得到同样的编码，逐字节可复现）。 */
    private final Map<Integer, Integer> codes = new LinkedHashMap<>();
    /** 编码 → 字形号。 */
    private final Map<Integer, Integer> glyphs = new LinkedHashMap<>();
    private int nextCode = FIRST_CODE;
    /** 编码用满之后的落脚点：替代符自己的编码。 */
    private int overflowCode;

    PdfFontUse(PdfFontProgram program) {
        this.program = program;
    }

    PdfFontProgram program() {
        return program;
    }

    /** 把文本编成 PDF 十六进制串（{@code <....>} 里的内容），顺带把用到的字记进账本。 */
    String encode(String text) {
        StringBuilder hex = new StringBuilder(text.length() * 4);
        text.codePoints().forEach(codePoint -> hex.append(String.format(Locale.ROOT, "%04X", codeOf(codePoint))));
        return hex.toString();
    }

    /** 文本在字号 {@code size} 下的宽度（PDF 用户单位）。 */
    double width(String text, double size) {
        double units = text.codePoints().map(this::advanceOf).sum();
        return units * size / program.unitsPerEm();
    }

    private int advanceOf(int codePoint) {
        return program.advanceOf(glyphFor(codePoint));
    }

    private int glyphFor(int codePoint) {
        int glyph = program.glyphOf(codePoint);
        return glyph == 0 ? program.substituteGlyph() : glyph;
    }

    /** 分配（或取回）某个码位的编码。 */
    private int codeOf(int codePoint) {
        Integer existing = codes.get(codePoint);
        if (existing != null) {
            return existing;
        }
        if (nextCode > LAST_CODE) {
            return overflow();
        }
        int code = nextCode++;
        codes.put(codePoint, code);
        glyphs.put(code, glyphFor(codePoint));
        return code;
    }

    /**
     * 一份文档里出现超过 65 535 个不同字符（现实里只可能是被灌了垃圾数据）时的落脚点：
     * 统一指向替代符。这时文本提取会退化成 {@code 〓}，但作业不会失败。
     */
    private int overflow() {
        if (overflowCode == 0) {
            overflowCode = codes.getOrDefault(PdfFontProgram.SUBSTITUTE_CODE_POINT, FIRST_CODE);
        }
        return overflowCode;
    }

    boolean isEmpty() {
        return codes.isEmpty();
    }

    /** 要嵌进 PDF 的字形号。 */
    Set<Integer> usedGlyphs() {
        return Set.copyOf(glyphs.values());
    }

    /** {@code /BaseFont} 用的名字，带子集标记。标记由字形集合的摘要算出，同样的输入得到同样的名字。 */
    String baseFont() {
        byte[] digest = sha256(new TreeSet<>(glyphs.values()).toString());
        StringBuilder tag = new StringBuilder(TAG_LETTERS);
        for (int i = 0; i < TAG_LETTERS; i++) {
            tag.append((char) ('A' + Math.floorMod(digest[i], 26)));
        }
        return tag + "+" + PdfFontProgram.POSTSCRIPT_NAME;
    }

    /** {@code /W} 数组：每个编码的步进宽度，换算到千分之一 em（PDF 的字形空间）。 */
    String widths() {
        StringBuilder widths = new StringBuilder("[");
        glyphs.forEach((code, glyph) -> widths.append(code).append(" [")
                .append(Math.round(program.advanceOf(glyph) * 1000.0 / program.unitsPerEm()))
                .append("] "));
        return widths.append("]").toString();
    }

    /** {@code CIDToGIDMap} 流：每个编码两字节大端的字形号，下标就是编码。 */
    byte[] cidToGidMap() {
        int highest = glyphs.keySet().stream().mapToInt(Integer::intValue).max().orElse(0);
        byte[] map = new byte[2 * (highest + 1)];
        glyphs.forEach((code, glyph) -> {
            map[2 * code] = (byte) (glyph >>> 8);
            map[2 * code + 1] = (byte) (int) glyph;
        });
        return map;
    }

    /**
     * {@code ToUnicode} CMap：编码 → 原始码位。**文本能不能从 PDF 里提取出来，全靠这一张表。**
     * 规范要求 {@code bfchar} 每段不超过 100 条，所以分段写。
     */
    byte[] toUnicodeCmap() {
        StringBuilder cmap = new StringBuilder("""
                /CIDInit /ProcSet findresource begin
                12 dict begin
                begincmap
                /CIDSystemInfo << /Registry (Adobe) /Ordering (UCS) /Supplement 0 >> def
                /CMapName /Adobe-Identity-UCS def
                /CMapType 2 def
                1 begincodespacerange
                <0000> <FFFF>
                endcodespacerange
                """);
        var entries = codes.entrySet().stream().toList();
        for (int from = 0; from < entries.size(); from += 100) {
            var chunk = entries.subList(from, Math.min(from + 100, entries.size()));
            cmap.append(chunk.size()).append(" beginbfchar\n");
            for (var entry : chunk) {
                cmap.append(String.format(Locale.ROOT, "<%04X> <%s>", entry.getValue(),
                        utf16beHex(entry.getKey()))).append('\n');
            }
            cmap.append("endbfchar\n");
        }
        cmap.append("""
                endcmap
                CMapName currentdict /CMap defineresource pop
                end
                end
                """);
        return cmap.toString().getBytes(StandardCharsets.US_ASCII);
    }

    /** 码位的 UTF-16BE 十六进制；辅助平面的字要写成代理对。 */
    private static String utf16beHex(int codePoint) {
        StringBuilder hex = new StringBuilder(8);
        for (char unit : Character.toChars(codePoint)) {
            hex.append(String.format(Locale.ROOT, "%04X", (int) unit));
        }
        return hex.toString();
    }

    private static byte[] sha256(String text) {
        try {
            return MessageDigest.getInstance("SHA-256").digest(text.getBytes(StandardCharsets.UTF_8));
        } catch (NoSuchAlgorithmException e) {
            throw new IllegalStateException("SHA-256 is required by every JDK", e);
        }
    }
}
