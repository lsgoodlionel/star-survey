package cn.mjy.platform.response;

import java.io.IOException;
import java.util.List;
import java.util.Objects;

/**
 * 导出文件里的一张表：CSV 包里的一个文件、XLSX 里的一个工作表、SAV 包里的一个数据集。
 * 行是流式给出的，写出方不持有整张表；{@link RowSource#forEach} 可以被调用多次，
 * 每次都从头重放同样的行（SAV 需要先扫一遍定变量宽度，再扫一遍写数据）。
 *
 * @param fileName   CSV 包内的文件名（不含扩展名），ASCII
 * @param title      工作表名（中文，≤ 31 字符）
 * @param headerRows 开头有几行是表头（不是数据）
 * @param variables  数据列的变量描述；只有答卷表有，别的表为空
 */
record ExportSheet(String fileName, String title, int headerRows, List<ExportVariable> variables, RowSource rows) {

    /** 按顺序把每一行交给 sink；单元格为 {@code null} 表示空。 */
    @FunctionalInterface
    interface RowSource {
        void forEach(RowSink sink) throws IOException;
    }

    @FunctionalInterface
    interface RowSink {
        void accept(List<String> cells) throws IOException;
    }

    ExportSheet {
        Objects.requireNonNull(fileName, "fileName");
        Objects.requireNonNull(title, "title");
        Objects.requireNonNull(rows, "rows");
        if (headerRows < 0) {
            throw new IllegalArgumentException("headerRows must not be negative");
        }
        variables = List.copyOf(variables);
    }

    /** 没有变量描述的附表（字段字典、附件清单）。 */
    static ExportSheet of(String fileName, String title, int headerRows, RowSource rows) {
        return new ExportSheet(fileName, title, headerRows, List.of(), rows);
    }
}
