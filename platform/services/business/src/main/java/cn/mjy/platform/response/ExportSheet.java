package cn.mjy.platform.response;

import java.io.IOException;
import java.util.List;
import java.util.Objects;

/**
 * 导出文件里的一张表：CSV 包里的一个文件、XLSX 里的一个工作表。行是流式给出的，写出方不持有整张表。
 *
 * @param fileName CSV 包内的文件名（不含扩展名），ASCII
 * @param title    工作表名（中文，≤ 31 字符）
 */
record ExportSheet(String fileName, String title, RowSource rows) {

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
    }
}
