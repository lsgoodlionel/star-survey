package cn.mjy.platform.response;

import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.UncheckedIOException;
import java.nio.charset.StandardCharsets;
import java.util.ArrayDeque;
import java.util.ArrayList;
import java.util.Deque;
import java.util.HashMap;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.SortedSet;
import java.util.TreeSet;

/**
 * 随交付自带的中文字体：解析 TrueType 表、按码位查字形、并按用到的字形裁出一份最小字体程序，
 * 供 PDF 以 {@code /FontFile2} <b>嵌入</b>（ADR 0015 增补五；冻结工作表 P-10 否决了靠阅读器
 * CJK 字体包那条备选）。
 *
 * <p>字体是 {@code resources/fonts/MJYSansSC-Regular.ttf}——Noto Sans CJK SC（思源黑体）的
 * GB2312 子集，OFL 1.1，来源与合规见同目录 {@code PROVENANCE.md}。整份 7 300 个字形约 2 MB，
 * 所以**不能**原样嵌进每一份 PDF：{@link #subset} 只留下这份文档真正用到的字形轮廓。
 *
 * <p>整个类不可变，只在类加载时读一次文件；{@link #subset} 不改任何状态，可以并发调用。
 */
final class PdfFontProgram {

    /** 类路径上的字体资源。 */
    private static final String RESOURCE = "/fonts/MJYSansSC-Regular.ttf";
    /** 改名后的 PostScript 名（见 PROVENANCE.md 第四节：OFL 保留字体名条款）。 */
    static final String POSTSCRIPT_NAME = "MJYSansSC";
    /** 子集外字符的替代字形（〓，U+3013）。 */
    static final int SUBSTITUTE_CODE_POINT = 0x3013;

    /** 嵌入时保留的表。PDF 里复合字体走 CIDToGIDMap，字体自己的 cmap 用不上，所以不留。 */
    private static final List<String> EMBEDDED_TABLES = List.of("glyf", "head", "hhea", "hmtx", "loca", "maxp");

    /** 复合字形的分量标志位。 */
    private static final int ARGS_ARE_WORDS = 0x0001;
    private static final int WE_HAVE_A_SCALE = 0x0008;
    private static final int MORE_COMPONENTS = 0x0020;
    private static final int WE_HAVE_AN_XY_SCALE = 0x0040;
    private static final int WE_HAVE_A_TWO_BY_TWO = 0x0080;

    private static final PdfFontProgram INSTANCE = new PdfFontProgram(read());

    private final byte[] font;
    private final Map<String, int[]> tables = new LinkedHashMap<>();
    private final int numGlyphs;
    private final int unitsPerEm;
    private final int numberOfHMetrics;
    /** 码位 → 字形号，取自 cmap 格式 4；只覆盖基本多文种平面（子集里没有平面外的字）。 */
    private final Map<Integer, Integer> cmap;
    /** 字形边界；FontDescriptor 要写。 */
    private final int[] bbox;
    private final int ascender;
    private final int descender;
    private final int substituteGlyph;

    private PdfFontProgram(byte[] font) {
        this.font = font;
        int count = u16(font, 4);
        for (int i = 0; i < count; i++) {
            int record = 12 + 16 * i;
            tables.put(new String(font, record, 4, StandardCharsets.US_ASCII),
                    new int[] {i32(font, record + 8), i32(font, record + 12)});
        }
        int head = table("head");
        int hhea = table("hhea");
        this.unitsPerEm = u16(font, head + 18);
        this.bbox = new int[] {s16(font, head + 36), s16(font, head + 38), s16(font, head + 40),
                s16(font, head + 42)};
        this.ascender = s16(font, hhea + 4);
        this.descender = s16(font, hhea + 6);
        this.numberOfHMetrics = u16(font, hhea + 34);
        this.numGlyphs = u16(font, table("maxp") + 4);
        this.cmap = readCmap();
        this.substituteGlyph = cmap.getOrDefault(SUBSTITUTE_CODE_POINT, 0);
        if (substituteGlyph == 0) {
            throw new IllegalStateException("字体里没有替代字形 U+3013，子集外的字符就无处可画了");
        }
    }

    static PdfFontProgram get() {
        return INSTANCE;
    }

    private static byte[] read() {
        try (InputStream in = PdfFontProgram.class.getResourceAsStream(RESOURCE)) {
            if (in == null) {
                throw new IllegalStateException("类路径上没有 " + RESOURCE + "：PDF 导出必须自带中文字体");
            }
            return in.readAllBytes();
        } catch (IOException e) {
            throw new UncheckedIOException("could not read the bundled CJK font", e);
        }
    }

    // ---- 查询 ----

    int unitsPerEm() {
        return unitsPerEm;
    }

    int ascender() {
        return ascender;
    }

    int descender() {
        return descender;
    }

    int[] boundingBox() {
        return bbox.clone();
    }

    int substituteGlyph() {
        return substituteGlyph;
    }

    /** @return 该码位的字形号；子集里没有这个字时返回 0 */
    int glyphOf(int codePoint) {
        return cmap.getOrDefault(codePoint, 0);
    }

    /** 字形的步进宽度（字体单位）。hmtx 末尾那一段共用最后一个宽度，是 TrueType 的常规压缩。 */
    int advanceOf(int glyph) {
        int hmtx = table("hmtx");
        int index = Math.min(glyph, numberOfHMetrics - 1);
        return index < 0 ? 0 : u16(font, hmtx + 4 * index);
    }

    // ---- 子集 ----

    /**
     * 裁出一份只含 {@code glyphs} 的字体程序。<b>字形号保持不变</b>（没用到的字形留成零长度），
     * 这样 CIDToGIDMap 里的字形号不必重编，复合字形的分量引用也不用改写。
     *
     * @param glyphs 用到的字形号；0（notdef）与复合字形的分量由这里自动补上
     */
    byte[] subset(Set<Integer> glyphs) {
        SortedSet<Integer> kept = withComponents(glyphs);
        int loca = table("loca");
        int glyf = table("glyf");
        boolean longLoca = s16(font, table("head") + 50) == 1;

        ByteArrayOutputStream outlines = new ByteArrayOutputStream();
        int[] offsets = new int[numGlyphs + 1];
        for (int glyph = 0; glyph < numGlyphs; glyph++) {
            offsets[glyph] = outlines.size();
            if (!kept.contains(glyph)) {
                continue;
            }
            int from = locaAt(loca, longLoca, glyph);
            int to = locaAt(loca, longLoca, glyph + 1);
            outlines.write(font, glyf + from, to - from);
            while (outlines.size() % 4 != 0) {
                outlines.write(0);
            }
        }
        offsets[numGlyphs] = outlines.size();

        Map<String, byte[]> embedded = new LinkedHashMap<>();
        embedded.put("glyf", outlines.toByteArray());
        embedded.put("loca", longLoca(offsets));
        embedded.put("head", head());
        embedded.put("hhea", copyOf("hhea"));
        embedded.put("hmtx", copyOf("hmtx"));
        embedded.put("maxp", copyOf("maxp"));
        return sfnt(embedded);
    }

    /** 补上 notdef 与复合字形递归引用到的分量——少了分量，那个字就画不全。 */
    private SortedSet<Integer> withComponents(Set<Integer> glyphs) {
        SortedSet<Integer> kept = new TreeSet<>();
        Deque<Integer> pending = new ArrayDeque<>();
        for (int glyph : glyphs) {
            if (glyph > 0 && glyph < numGlyphs && kept.add(glyph)) {
                pending.add(glyph);
            }
        }
        kept.add(0);
        pending.add(0);
        while (!pending.isEmpty()) {
            for (int component : componentsOf(pending.poll())) {
                if (component < numGlyphs && kept.add(component)) {
                    pending.add(component);
                }
            }
        }
        return kept;
    }

    /** 复合字形直接引用到的分量字形号；简单字形返回空。 */
    private List<Integer> componentsOf(int glyph) {
        int loca = table("loca");
        boolean longLoca = s16(font, table("head") + 50) == 1;
        int from = locaAt(loca, longLoca, glyph);
        int to = locaAt(loca, longLoca, glyph + 1);
        if (to - from < 10 || s16(font, table("glyf") + from) >= 0) {
            return List.of();
        }
        List<Integer> components = new ArrayList<>(2);
        int at = table("glyf") + from + 10;
        int flags;
        do {
            flags = u16(font, at);
            components.add(u16(font, at + 2));
            at += 4 + ((flags & ARGS_ARE_WORDS) != 0 ? 4 : 2);
            if ((flags & WE_HAVE_A_TWO_BY_TWO) != 0) {
                at += 8;
            } else if ((flags & WE_HAVE_AN_XY_SCALE) != 0) {
                at += 4;
            } else if ((flags & WE_HAVE_A_SCALE) != 0) {
                at += 2;
            }
        } while ((flags & MORE_COMPONENTS) != 0);
        return components;
    }

    /** head 照抄，只把 indexToLocFormat 改成长格式、把整档校验和清零（阅读器不核对它）。 */
    private byte[] head() {
        byte[] head = copyOf("head");
        for (int i = 8; i < 12; i++) {
            head[i] = 0;
        }
        head[50] = 0;
        head[51] = 1;
        return head;
    }

    private static byte[] longLoca(int[] offsets) {
        byte[] loca = new byte[4 * offsets.length];
        for (int i = 0; i < offsets.length; i++) {
            put32(loca, 4 * i, offsets[i]);
        }
        return loca;
    }

    /** 拼出 sfnt 容器：表目录按表名字典序，每张表 4 字节对齐，校验和按规范算。 */
    private static byte[] sfnt(Map<String, byte[]> embedded) {
        int count = embedded.size();
        int entrySelector = 31 - Integer.numberOfLeadingZeros(count);
        int searchRange = (1 << entrySelector) * 16;
        byte[] header = new byte[12 + 16 * count];
        put32(header, 0, 0x00010000);
        put16(header, 4, count);
        put16(header, 6, searchRange);
        put16(header, 8, entrySelector);
        put16(header, 10, count * 16 - searchRange);

        ByteArrayOutputStream body = new ByteArrayOutputStream();
        int record = 12;
        int offset = header.length;
        for (String tag : EMBEDDED_TABLES) {
            byte[] table = embedded.get(tag);
            System.arraycopy(tag.getBytes(StandardCharsets.US_ASCII), 0, header, record, 4);
            put32(header, record + 4, (int) checksum(table));
            put32(header, record + 8, offset);
            put32(header, record + 12, table.length);
            body.writeBytes(table);
            while (body.size() % 4 != 0) {
                body.write(0);
            }
            offset = header.length + body.size();
            record += 16;
        }
        ByteArrayOutputStream out = new ByteArrayOutputStream(header.length + body.size());
        out.writeBytes(header);
        out.writeBytes(body.toByteArray());
        return out.toByteArray();
    }

    private static long checksum(byte[] table) {
        long sum = 0;
        for (int i = 0; i < table.length; i += 4) {
            long word = 0;
            for (int b = 0; b < 4; b++) {
                word = (word << 8) | (i + b < table.length ? table[i + b] & 0xFF : 0);
            }
            sum = (sum + word) & 0xFFFFFFFFL;
        }
        return sum;
    }

    // ---- 表与整数 ----

    private int table(String tag) {
        int[] entry = tables.get(tag);
        if (entry == null) {
            throw new IllegalStateException("字体里没有 " + tag + " 表");
        }
        return entry[0];
    }

    private byte[] copyOf(String tag) {
        int[] entry = tables.get(tag);
        byte[] copy = new byte[entry[1]];
        System.arraycopy(font, entry[0], copy, 0, entry[1]);
        return copy;
    }

    private int locaAt(int loca, boolean longFormat, int glyph) {
        return longFormat ? i32(font, loca + 4 * glyph) : u16(font, loca + 2 * glyph) * 2;
    }

    /** cmap 格式 4（Windows Unicode BMP）：按段展开成一张表，查的时候就不必二分了。 */
    private Map<Integer, Integer> readCmap() {
        int cmap = table("cmap");
        int subtable = -1;
        for (int i = 0; i < u16(font, cmap + 2); i++) {
            int record = cmap + 4 + 8 * i;
            if (u16(font, record) == 3 && u16(font, record + 2) == 1) {
                subtable = cmap + i32(font, record + 4);
            }
        }
        if (subtable < 0 || u16(font, subtable) != 4) {
            throw new IllegalStateException("字体缺少 (3,1) 格式 4 的 cmap 子表");
        }
        int segments = u16(font, subtable + 6) / 2;
        int ends = subtable + 14;
        int starts = ends + segments * 2 + 2;
        int deltas = starts + segments * 2;
        int ranges = deltas + segments * 2;
        Map<Integer, Integer> codes = new HashMap<>(segments * 8);
        for (int segment = 0; segment < segments; segment++) {
            int end = u16(font, ends + 2 * segment);
            int start = u16(font, starts + 2 * segment);
            int delta = s16(font, deltas + 2 * segment);
            int rangeOffset = u16(font, ranges + 2 * segment);
            for (int code = start; code <= end && code != 0xFFFF; code++) {
                int glyph;
                if (rangeOffset == 0) {
                    glyph = (code + delta) & 0xFFFF;
                } else {
                    int at = ranges + 2 * segment + rangeOffset + 2 * (code - start);
                    glyph = u16(font, at);
                    glyph = glyph == 0 ? 0 : (glyph + delta) & 0xFFFF;
                }
                if (glyph != 0) {
                    codes.put(code, glyph);
                }
            }
        }
        return Map.copyOf(codes);
    }

    private static int u16(byte[] data, int at) {
        return ((data[at] & 0xFF) << 8) | (data[at + 1] & 0xFF);
    }

    private static int s16(byte[] data, int at) {
        return (short) u16(data, at);
    }

    private static int i32(byte[] data, int at) {
        return (u16(data, at) << 16) | u16(data, at + 2);
    }

    private static void put16(byte[] data, int at, int value) {
        data[at] = (byte) (value >>> 8);
        data[at + 1] = (byte) value;
    }

    private static void put32(byte[] data, int at, int value) {
        put16(data, at, value >>> 16);
        put16(data, at + 2, value & 0xFFFF);
    }
}
