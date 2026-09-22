package cn.mjy.platform.response;

import com.fasterxml.jackson.annotation.JsonValue;
import java.io.IOException;
import java.io.OutputStream;
import java.util.Arrays;
import java.util.List;
import java.util.Locale;
import java.util.Optional;

/**
 * 本切片支持的导出格式（ADR 0015 决定 5）。新格式（SPSS SAV、Word/PDF 模板）在这里加一项并实现
 * {@link Writer}：写出方拿到的是与格式无关的表（答卷、数据字典、附件清单），行流式给出。
 */
public enum ExportFormat {

    /** ZIP 包：responses.csv、fields.csv、attachments.csv，UTF-8 带 BOM。 */
    CSV("csv", "application/zip", "zip", new CsvExportWriter()),
    /** 最小 OOXML 工作簿，三个工作表。 */
    XLSX("xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "xlsx",
            new XlsxExportWriter());

    /** 把若干张表写成一个文件；只写 out，不关闭它。 */
    interface Writer {
        void write(List<ExportSheet> sheets, OutputStream out) throws IOException;

        /** 数据行（不含表头）的上限；超过时作业失败而不是截断。 */
        default long maxDataRows() {
            return Long.MAX_VALUE;
        }
    }

    private final String code;
    private final String contentType;
    private final String extension;
    private final Writer writer;

    ExportFormat(String code, String contentType, String extension, Writer writer) {
        this.code = code;
        this.contentType = contentType;
        this.extension = extension;
        this.writer = writer;
    }

    @JsonValue
    public String code() {
        return code;
    }

    public String contentType() {
        return contentType;
    }

    public String extension() {
        return extension;
    }

    Writer writer() {
        return writer;
    }

    public static Optional<ExportFormat> fromCode(String code) {
        if (code == null) {
            return Optional.empty();
        }
        String lowered = code.toLowerCase(Locale.ROOT);
        return Arrays.stream(values()).filter(f -> f.code.equals(lowered)).findFirst();
    }
}
