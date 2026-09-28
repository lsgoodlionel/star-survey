package cn.mjy.platform.response;

import java.io.IOException;
import java.io.Writer;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;

/**
 * 把一份答卷的扩展副表单元格透视回原来的表格，写进 Word 文档。
 *
 * <p>导出的 {@code extensions} 长表是一个单元格一行（ADR 0015 增补二）：表格格式的列集必须在冻结
 * 表头时就定死，而副表的列字典平台手上没有。文档格式没有这个约束——它一次只持有一份答卷，
 * 这一份里出现过哪些列代码是现成的，于是这里能还原成「一行一行、一列一列」的真表格。
 *
 * <p>闸门没过的那一题不写数据表，改写一句话：引擎答卷列里还留着不可信的原文，
 * 一张空表会被读成「作答者没填」（契约 question-extension-tables-v1 第三节第 1 条）。
 */
final class ExtensionTables {

    /** {@link ExportLayout#EXTENSION_HEADER} 里各列的下标。 */
    private static final int QUESTION = 4;
    private static final int STRUCTURE_VERSION = 5;
    private static final int IS_VALID = 6;
    private static final int ROW_INDEX = 7;
    private static final int COLUMN = 8;
    private static final int VALUE = 9;

    private static final String ROW_HEADER = "行";
    private static final String INVALID = "未通过校验，副表里没有作答；引擎答卷列里的原文不可信。";
    private static final String NO_ROWS = "没有任何行。";

    /** 一道副表题在这一份答卷里的全部单元格。 */
    private static final class Question {

        private final String structureVersion;
        private final boolean valid;
        /** 列代码按首次出现的顺序，即插件写入时的列字典顺序。 */
        private final Set<String> columns = new LinkedHashSet<>();
        /** 行序号 → (列代码 → 值)。 */
        private final Map<String, Map<String, String>> rows = new LinkedHashMap<>();

        private Question(String structureVersion, boolean valid) {
            this.structureVersion = structureVersion;
            this.valid = valid;
        }
    }

    private ExtensionTables() {
    }

    /**
     * 透视结果，与最终格式无关：Word 与 PDF 拿到的是同一份（DRY——这段分组与补空格的逻辑
     * 只该有一份实现）。
     *
     * @param title 小节标题，已含「（结构版本 …）」
     * @param note  没有数据表时要写的那句话；有表时为空串
     * @param rows  表头行在最前，其余是数据行；{@code note} 非空时为空表
     */
    record Pivoted(String title, String note, List<List<String>> rows) {

        Pivoted {
            rows = List.copyOf(rows);
        }
    }

    /** 把一份答卷的副表单元格透视成若干张表，一道副表题一张。 */
    static List<Pivoted> pivot(ExportContent content, ExportRecord record) {
        List<Pivoted> pivoted = new ArrayList<>();
        for (Map.Entry<String, Question> entry : group(record.extensions()).entrySet()) {
            Question question = entry.getValue();
            String title = title(content, entry.getKey()) + "（结构版本 " + question.structureVersion + "）";
            if (question.rows.isEmpty()) {
                pivoted.add(new Pivoted(title, question.valid ? NO_ROWS : INVALID, List.of()));
                continue;
            }
            pivoted.add(new Pivoted(title, "", rows(question)));
        }
        return List.copyOf(pivoted);
    }

    private static List<List<String>> rows(Question question) {
        List<String> columns = List.copyOf(question.columns);
        List<List<String>> rows = new ArrayList<>(question.rows.size() + 1);
        List<String> header = new ArrayList<>(columns.size() + 1);
        header.add(ROW_HEADER);
        header.addAll(columns);
        rows.add(List.copyOf(header));
        for (Map.Entry<String, Map<String, String>> row : question.rows.entrySet()) {
            List<String> cells = new ArrayList<>(columns.size() + 1);
            cells.add(display(row.getKey()));
            columns.forEach(column -> cells.add(row.getValue().getOrDefault(column, "")));
            rows.add(List.copyOf(cells));
        }
        return rows;
    }

    static void write(Writer writer, ExportContent content, ExportRecord record) throws IOException {
        for (Pivoted question : pivot(content, record)) {
            DocxExportWriter.heading(writer, 2, question.title());
            if (!question.note().isEmpty()) {
                DocxExportWriter.paragraph(writer, question.note());
                continue;
            }
            DocxExportWriter.tableStart(writer);
            for (List<String> row : question.rows()) {
                DocxExportWriter.row(writer, row);
            }
            DocxExportWriter.tableEnd(writer);
        }
    }

    /** 行序号从 0 起（契约口径），给人看的表里从 1 起。 */
    private static String display(String rowIndex) {
        try {
            return Integer.toString(Integer.parseInt(rowIndex) + 1);
        } catch (NumberFormatException e) {
            return rowIndex;
        }
    }

    /** 题目代码 → 这一题的单元格；行序为空的那一行是「没有行」的标记，不当成数据行。 */
    private static Map<String, Question> group(List<List<String>> cells) {
        Map<String, Question> byQuestion = new LinkedHashMap<>();
        for (List<String> cell : cells) {
            if (cell.size() <= VALUE) {
                continue;
            }
            Question question = byQuestion.computeIfAbsent(cell.get(QUESTION),
                    code -> new Question(cell.get(STRUCTURE_VERSION), Boolean.parseBoolean(cell.get(IS_VALID))));
            String rowIndex = cell.get(ROW_INDEX);
            if (rowIndex == null || rowIndex.isEmpty()) {
                continue;
            }
            question.columns.add(cell.get(COLUMN));
            question.rows.computeIfAbsent(rowIndex, index -> new LinkedHashMap<>())
                    .put(cell.get(COLUMN), cell.get(VALUE));
        }
        return byQuestion;
    }

    /** 题目的中文标签取自答卷表表头（那一列是这道题的 JSON 信封）；找不到就用题目代码。 */
    private static String title(ExportContent content, String questionCode) {
        int index = content.codes().indexOf(questionCode);
        return index >= 0 && index < content.labels().size() ? content.labels().get(index) : questionCode;
    }
}
