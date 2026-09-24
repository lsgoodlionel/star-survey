package cn.mjy.platform.response;

import java.util.List;
import java.util.Objects;

/**
 * 交给 {@link ExportFormat.Writer} 的内容，与最终格式无关。同一份数据的两个视图，都可以重复重放：
 *
 * <ul>
 *   <li>{@code sheets}：若干张表（答卷、字段字典、附件清单、扩展表作答），表格格式按表出列；</li>
 *   <li>{@code records}：逐份答卷的记录（答卷行＋属于它的附件与副表单元格），
 *       逐份成文的格式（Word / PDF）按它排版。</li>
 * </ul>
 *
 * @param codes  答卷表表头第一行（列代码），与 {@link ExportRecord#cells()} 按下标对应
 * @param labels 答卷表表头第二行（中文标签），同上
 */
record ExportContent(List<ExportSheet> sheets, List<String> codes, List<String> labels, ExportRecord.Source records) {

    ExportContent {
        sheets = List.copyOf(sheets);
        codes = List.copyOf(codes);
        labels = List.copyOf(labels);
        Objects.requireNonNull(records, "records");
    }

    /** 只有表、没有记录流：表格格式的单元测试用。 */
    static ExportContent ofSheets(List<ExportSheet> sheets) {
        return new ExportContent(sheets, List.of(), List.of(), sink -> {
        });
    }

    /** 答卷表；写出方约定它永远是第一张。 */
    ExportSheet responses() {
        return sheets().getFirst();
    }
}
