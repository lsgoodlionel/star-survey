package cn.mjy.platform.response;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.List;
import java.util.Locale;

/**
 * PDF 的排版层：标题、段落、表格三种块，自己折行、自己翻页。
 * 一次只持有<b>当前这一页</b>的内容流，写满就交给 {@link PageSink} 并开新页——
 * 内存占用与总页数无关（ADR 0015 硬约束）。
 *
 * <p>折行按字符宽度累加：中文可以在任意两字之间断，西文优先在空格处断（{@link #wrap}）。
 * 没有连字、没有双向文本、没有竖排——导出文档要的是「读得到、复制得出」，不是排版软件。
 */
final class PdfPageWriter {

    /** 一页画完了。 */
    @FunctionalInterface
    interface PageSink {
        void accept(byte[] content) throws IOException;
    }

    /** A4，单位是 PDF 用户空间的点（1/72 英寸）。 */
    static final double PAGE_WIDTH = 595.28;
    static final double PAGE_HEIGHT = 841.89;
    private static final double MARGIN = 56.7;
    private static final double BODY_SIZE = 8.5;
    private static final double[] HEADING_SIZE = {0, 15, 10.5};
    private static final double LINE_RATIO = 1.45;
    /** 表格单元格的内边距与线宽。 */
    private static final double CELL_PADDING = 3;
    private static final double LINE_WIDTH = 0.4;
    /** 一列至少留这么宽，否则窄屏下会挤成一个字一行。 */
    private static final double MIN_COLUMN = 36;
    private static final double BLOCK_GAP = 5;

    private final PdfFontUse font;
    private final PageSink sink;
    private final StringBuilder content = new StringBuilder();
    private double y = PAGE_HEIGHT - MARGIN;
    private int pages;

    PdfPageWriter(PdfFontUse font, PageSink sink) {
        this.font = font;
        this.sink = sink;
    }

    int pageCount() {
        return pages;
    }

    /** 当前页有没有画过东西。 */
    private boolean isBlank() {
        return content.isEmpty();
    }

    /** 强制翻页；当前页是空的就不翻（免得开头多一张白纸）。 */
    void newPage() throws IOException {
        if (!isBlank()) {
            flush();
        }
    }

    /** 把当前页交出去，重置光标。 */
    private void flush() throws IOException {
        sink.accept(content.toString().getBytes(StandardCharsets.ISO_8859_1));
        content.setLength(0);
        y = PAGE_HEIGHT - MARGIN;
        pages++;
    }

    /** 收尾：最后一页还有内容就交出去。 */
    void finish() throws IOException {
        if (!isBlank()) {
            flush();
        }
    }

    // ---- 块 ----

    void heading(int level, String text) throws IOException {
        double size = HEADING_SIZE[level];
        if (!isBlank()) {
            space(size * 0.6);
        }
        for (String line : wrap(text, contentWidth(), size)) {
            line(line, MARGIN, size);
        }
        space(BLOCK_GAP);
    }

    void paragraph(String text) throws IOException {
        for (String line : wrap(text, contentWidth(), BODY_SIZE)) {
            line(line, MARGIN, BODY_SIZE);
        }
        space(BLOCK_GAP);
    }

    /**
     * 一张有边框的表。行高按最高的那一格算；一行放不下就翻页接着画
     * （不重复表头——重复表头会让「这一行属于哪一份答卷」更难对，得不偿失）。
     */
    void table(List<List<String>> rows) throws IOException {
        if (rows.isEmpty()) {
            return;
        }
        double[] widths = columnWidths(rows);
        for (List<String> row : rows) {
            List<List<String>> cells = new ArrayList<>(row.size());
            int lines = 1;
            for (int column = 0; column < row.size(); column++) {
                List<String> wrapped = wrap(guarded(row.get(column)),
                        widths[column] - 2 * CELL_PADDING, BODY_SIZE);
                cells.add(wrapped);
                lines = Math.max(lines, wrapped.size());
            }
            double height = lines * BODY_SIZE * LINE_RATIO + 2 * CELL_PADDING;
            if (y - height < MARGIN) {
                flush();
            }
            row(cells, widths, height);
        }
        space(BLOCK_GAP);
    }

    private void row(List<List<String>> cells, double[] widths, double height) {
        double top = y;
        double x = MARGIN;
        for (int column = 0; column < cells.size(); column++) {
            rectangle(x, top - height, widths[column], height);
            double textY = top - CELL_PADDING - BODY_SIZE;
            for (String line : cells.get(column)) {
                text(line, x + CELL_PADDING, textY, BODY_SIZE);
                textY -= BODY_SIZE * LINE_RATIO;
            }
            x += widths[column];
        }
        y = top - height;
    }

    /** 列宽：先按各列最宽的一格算，超出版面就按比例缩，缩到下限为止。 */
    private double[] columnWidths(List<List<String>> rows) {
        int columns = rows.stream().mapToInt(List::size).max().orElse(1);
        double[] widths = new double[columns];
        for (List<String> row : rows) {
            for (int column = 0; column < row.size(); column++) {
                widths[column] = Math.max(widths[column],
                        font.width(guarded(row.get(column)), BODY_SIZE) + 2 * CELL_PADDING);
            }
        }
        double total = 0;
        for (double width : widths) {
            total += width;
        }
        double available = contentWidth();
        if (total <= available) {
            return widths;
        }
        double scale = available / total;
        double scaled = 0;
        for (int column = 0; column < columns; column++) {
            widths[column] = Math.max(MIN_COLUMN, widths[column] * scale);
            scaled += widths[column];
        }
        // 抬到下限之后可能又超了；再按比例压一次，这一轮不设下限，保证画不出版面外。
        if (scaled > available) {
            for (int column = 0; column < columns; column++) {
                widths[column] *= available / scaled;
            }
        }
        return widths;
    }

    private static String guarded(String value) {
        return ExportCellGuard.guard(value);
    }

    // ---- 原语 ----

    private static double contentWidth() {
        return PAGE_WIDTH - 2 * MARGIN;
    }

    /** 留出垂直间距；剩下的地方不够就直接翻页（新页从顶上开始，不必再留）。 */
    private void space(double height) throws IOException {
        if (y - height < MARGIN) {
            flush();
            return;
        }
        y -= height;
    }

    /** 画一行文字并下移一行；页底了就先翻页。 */
    private void line(String text, double x, double size) throws IOException {
        if (y - size * LINE_RATIO < MARGIN) {
            flush();
        }
        y -= size * LINE_RATIO;
        text(text, x, y, size);
    }

    private void text(String text, double x, double baseline, double size) {
        if (text.isEmpty()) {
            return;
        }
        content.append("BT /F1 ").append(number(size)).append(" Tf 1 0 0 1 ")
                .append(number(x)).append(' ').append(number(baseline)).append(" Tm <")
                .append(font.encode(text)).append("> Tj ET\n");
    }

    private void rectangle(double x, double bottom, double width, double height) {
        content.append(number(LINE_WIDTH)).append(" w ").append(number(x)).append(' ')
                .append(number(bottom)).append(' ').append(number(width)).append(' ')
                .append(number(height)).append(" re S\n");
    }

    /** 固定两位小数、固定区域设置：坐标的写法也要逐字节相同。 */
    private static String number(double value) {
        return String.format(Locale.ROOT, "%.2f", value);
    }

    // ---- 折行 ----

    /**
     * 按可用宽度折行。中文在任意两字之间断得开；西文尽量在空格处断，一个词比一行还长时照样硬断。
     * 文本里的换行符是硬换行。
     */
    List<String> wrap(String text, double available, double size) {
        List<String> lines = new ArrayList<>();
        for (String hard : text.split("\n", -1)) {
            lines.addAll(wrapOneLine(hard, Math.max(available, size), size));
        }
        return lines;
    }

    private List<String> wrapOneLine(String text, double available, double size) {
        List<String> lines = new ArrayList<>();
        StringBuilder current = new StringBuilder();
        double used = 0;
        int lastBreak = -1;
        for (int i = 0; i < text.length(); ) {
            int codePoint = text.codePointAt(i);
            i += Character.charCount(codePoint);
            String candidate = new String(Character.toChars(codePoint));
            double advance = font.width(candidate, size);
            if (used + advance > available && !current.isEmpty()) {
                int cut = lastBreak > 0 ? lastBreak : current.length();
                lines.add(current.substring(0, cut).stripTrailing());
                current.delete(0, cut);
                used = font.width(current.toString(), size);
                lastBreak = -1;
            }
            current.append(candidate);
            used += advance;
            if (canBreakAfter(codePoint)) {
                lastBreak = current.length();
            }
        }
        lines.add(current.toString());
        return lines;
    }

    /** 中日韩字与全角标点后面都能断行；西文只在空白与连字符后断。 */
    private static boolean canBreakAfter(int codePoint) {
        return Character.isWhitespace(codePoint) || codePoint == '-' || codePoint > 0x2E7F;
    }
}
