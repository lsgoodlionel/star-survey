package cn.mjy.platform.response;

import cn.mjy.platform.response.FieldDictionary.OptionLabel;
import java.io.IOException;
import java.io.OutputStream;
import java.util.ArrayList;
import java.util.List;
import java.util.stream.Collectors;

/**
 * SPSS 导出包（R06-03）：
 *
 * <ul>
 *   <li>{@code responses.sav}：数据集本身，变量标签与值标签写在文件自带的字典里；</li>
 *   <li>{@code variables.csv}：变量字典的可读副本——SPSS 变量名与原始列代码的对应、是数值还是文本、
 *       宽度，以及<b>完整</b>选项表（挂不进 .sav 的那些也在，见 {@link SavVariable#labelledOptions()}）；</li>
 *   <li>{@code fields.csv}、{@code attachments.csv}：与 CSV 包里的同名文件一致。</li>
 * </ul>
 *
 * <p>答卷表的行会被重放两遍：第一遍定变量宽度，第二遍写数据（{@link SavDictionary}）。
 */
final class SavExportWriter implements ExportFormat.Writer {

    static final String DATASET_ENTRY = "responses.sav";
    static final String VARIABLES_ENTRY = "variables";
    static final List<String> VARIABLES_HEADER =
            List.of("name", "code", "label", "measure", "width", "valuelabels");
    private static final String OPTION_SEPARATOR = "; ";

    /** SPSS 的记录数写在一个 32 位字段里。 */
    @Override
    public long maxDataRows() {
        return Integer.MAX_VALUE;
    }

    @Override
    public void write(ExportContent content, OutputStream out) throws IOException {
        List<ExportSheet> sheets = content.sheets();
        if (sheets.isEmpty()) {
            throw new IOException("a sav bundle needs at least the responses sheet");
        }
        ExportSheet responses = sheets.getFirst();
        SavDictionary dictionary = SavDictionary.of(responses);
        try (ExportZip zip = new ExportZip(out)) {
            try (OutputStream entry = zip.entry(DATASET_ENTRY)) {
                SavFile.write(responses, dictionary, entry);
            }
            csv(zip, ExportSheet.of(VARIABLES_ENTRY, "变量字典", 1, sink -> {
                for (List<String> row : variableRows(dictionary)) {
                    sink.accept(row);
                }
            }));
            for (ExportSheet sheet : sheets.subList(1, sheets.size())) {
                csv(zip, sheet);
            }
        }
    }

    private static void csv(ExportZip zip, ExportSheet sheet) throws IOException {
        try (OutputStream entry = zip.entry(sheet.fileName() + ".csv")) {
            CsvExportWriter.writeSheet(sheet, entry);
        }
    }

    static List<List<String>> variableRows(SavDictionary dictionary) {
        List<List<String>> rows = new ArrayList<>(dictionary.variables().size() + 1);
        rows.add(VARIABLES_HEADER);
        for (SavVariable variable : dictionary.variables()) {
            rows.add(List.of(variable.name(), variable.code(), variable.label(), variable.measure(),
                    Integer.toString(variable.reportedWidth()), options(variable.options())));
        }
        return List.copyOf(rows);
    }

    private static String options(List<OptionLabel> options) {
        return options.stream().map(o -> o.code() + "=" + o.text()).collect(Collectors.joining(OPTION_SEPARATOR));
    }
}
