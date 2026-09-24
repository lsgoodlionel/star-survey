package cn.mjy.platform.response;

import com.fasterxml.jackson.annotation.JsonValue;
import java.io.IOException;
import java.io.OutputStream;
import java.util.Arrays;
import java.util.Locale;
import java.util.Optional;

/**
 * 支持的导出格式（ADR 0015 决定 5；切片 06.3 增补 SAV，06.4 增补 DOCX）。新格式在这里加一项并实现
 * {@link Writer}：写出方拿到的是与格式无关的 {@link ExportContent}——既可以按表出列
 * （答卷、字段字典、附件清单、扩展表作答），也可以逐份答卷成文，两个视图都能重复重放
 * （SAV 要扫两遍：先定变量宽度，再写数据）。
 *
 * <p>加新格式时别忘了迁移里 {@code response_export_job.format} 的取值约束。
 */
public enum ExportFormat {

    /** ZIP 包：responses.csv、fields.csv、attachments.csv，UTF-8 带 BOM。 */
    CSV("csv", "application/zip", "zip", new CsvExportWriter()),
    /** 最小 OOXML 工作簿，三个工作表。 */
    XLSX("xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "xlsx",
            new XlsxExportWriter()),
    /** ZIP 包：SPSS 系统文件 responses.sav（自带变量字典）加三张 CSV 附表。 */
    SAV("sav", "application/zip", "sav.zip", new SavExportWriter()),
    /** 逐份答卷成文的 Word 文档：每份答卷一页，题目与作答成对呈现（R06-05 / R06-06）。 */
    DOCX("docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document", "docx",
            new DocxExportWriter());

    /** 把导出内容写成一个文件；只写 out，不关闭它。 */
    interface Writer {
        void write(ExportContent content, OutputStream out) throws IOException;

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
