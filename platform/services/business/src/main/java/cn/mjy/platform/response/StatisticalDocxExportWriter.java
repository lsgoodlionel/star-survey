package cn.mjy.platform.response;

import java.io.BufferedWriter;
import java.io.IOException;
import java.io.OutputStream;
import java.io.OutputStreamWriter;
import java.io.Writer;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;

/**
 * Word 统计报告（R06-04）：对答卷字段逐列输出有效值、空值、值频数和有效值内比例。
 *
 * <p>内存上界由字段数和 {@link #MAX_DISTINCT_VALUES} 决定，不随答卷量或自由文本基数增长。每列只保留
 * 最先出现的固定数量不同值；后续未被保留的值统一计入“其他”。答卷快照本身由导出作业冻结，本 writer
 * 只重复读取该快照，不查询运行中的答卷表。
 */
final class StatisticalDocxExportWriter implements ExportFormat.Writer {

    static final int MAX_DISTINCT_VALUES = 100;
    static final long MAX_RESPONSES = 1_000_000L;

    private static final String XML_DECL = "<?xml version=\"1.0\" encoding=\"UTF-8\" standalone=\"yes\"?>\n";
    private static final String NS_W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main";
    private static final String NS_PKG_REL = "http://schemas.openxmlformats.org/package/2006/relationships";
    private static final String REL_TYPE = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/";

    @Override
    public long maxDataRows() {
        return MAX_RESPONSES;
    }

    @Override
    public void write(ExportContent content, OutputStream out) throws IOException {
        write(content, out, ExportWriteBudget.unlimited());
    }

    @Override
    public void write(ExportContent content, OutputStream out, ExportWriteBudget budget) throws IOException {
        Statistics statistics = collect(content, budget);
        budget.checkpoint();
        try (ExportZip zip = new ExportZip(out)) {
            text(zip, "[Content_Types].xml", contentTypes());
            text(zip, "_rels/.rels", rootRels());
            text(zip, "docProps/app.xml", appProps());
            try (OutputStream entry = zip.entry("word/document.xml")) {
                document(content, statistics, entry, budget);
            }
            text(zip, "word/_rels/document.xml.rels", documentRels());
            text(zip, "word/styles.xml", styles());
        }
    }

    private static Statistics collect(ExportContent content, ExportWriteBudget budget) throws IOException {
        int answerStart = Math.min(ExportLayout.FIXED_CODES.size(), content.codes().size());
        List<ColumnStatistics> columns = new ArrayList<>();
        for (int index = answerStart; index < content.codes().size(); index++) {
            columns.add(new ColumnStatistics());
        }
        long[] total = {0L};
        content.records().forEach(record -> {
            budget.checkpoint();
            total[0]++;
            for (int offset = 0; offset < columns.size(); offset++) {
                int index = answerStart + offset;
                String value = index < record.cells().size() ? record.cells().get(index) : null;
                columns.get(offset).accept(value);
            }
        });
        return new Statistics(total[0], answerStart, List.copyOf(columns));
    }

    private static void document(ExportContent content, Statistics statistics, OutputStream out,
            ExportWriteBudget budget) throws IOException {
        Writer writer = new BufferedWriter(new OutputStreamWriter(out, StandardCharsets.UTF_8));
        writer.write(XML_DECL);
        writer.write("<w:document xmlns:w=\"" + NS_W + "\"><w:body>");
        DocxExportWriter.heading(writer, 1, "答卷统计报告");
        DocxExportWriter.tableStart(writer);
        DocxExportWriter.row(writer, List.of("指标", "数值"));
        DocxExportWriter.row(writer, List.of("答卷总数", Long.toString(statistics.total())));
        DocxExportWriter.tableEnd(writer);

        for (int offset = 0; offset < statistics.columns().size(); offset++) {
            budget.checkpoint();
            int index = statistics.answerStart() + offset;
            String label = index < content.labels().size() && !content.labels().get(index).isBlank()
                    ? content.labels().get(index) : content.codes().get(index);
            ColumnStatistics column = statistics.columns().get(offset);
            long valid = statistics.total() - column.missing;
            DocxExportWriter.heading(writer, 2, label);
            DocxExportWriter.tableStart(writer);
            DocxExportWriter.row(writer, List.of("统计项", "数量"));
            DocxExportWriter.row(writer, List.of("有效值", Long.toString(valid)));
            DocxExportWriter.row(writer, List.of("空值", Long.toString(column.missing)));
            DocxExportWriter.tableEnd(writer);

            DocxExportWriter.tableStart(writer);
            DocxExportWriter.row(writer, List.of("值", "频数", "比例"));
            for (Map.Entry<String, Long> value : column.frequencies.entrySet()) {
                budget.checkpoint();
                DocxExportWriter.row(writer, List.of(value.getKey(), Long.toString(value.getValue()),
                        percent(value.getValue(), valid)));
            }
            if (column.other > 0) {
                DocxExportWriter.row(writer,
                        List.of("其他", Long.toString(column.other), percent(column.other, valid)));
            }
            DocxExportWriter.tableEnd(writer);
        }
        writer.write("<w:sectPr/></w:body></w:document>");
        writer.flush();
    }

    private static String percent(long count, long total) {
        return total == 0 ? "0.00%" : String.format(Locale.ROOT, "%.2f%%", count * 100.0 / total);
    }

    private static void text(ExportZip zip, String name, String content) throws IOException {
        try (OutputStream entry = zip.entry(name)) {
            entry.write(content.getBytes(StandardCharsets.UTF_8));
        }
    }

    private static String contentTypes() {
        return XML_DECL + "<Types xmlns=\"http://schemas.openxmlformats.org/package/2006/content-types\">"
                + "<Default Extension=\"rels\" ContentType=\"application/vnd.openxmlformats-package.relationships+xml\"/>"
                + "<Default Extension=\"xml\" ContentType=\"application/xml\"/>"
                + "<Override PartName=\"/word/document.xml\" ContentType=\"application/vnd.openxmlformats-"
                + "officedocument.wordprocessingml.document.main+xml\"/>"
                + "<Override PartName=\"/word/styles.xml\" ContentType=\"application/vnd.openxmlformats-"
                + "officedocument.wordprocessingml.styles+xml\"/>"
                + "<Override PartName=\"/docProps/app.xml\" ContentType=\"application/vnd.openxmlformats-"
                + "officedocument.extended-properties+xml\"/></Types>";
    }

    private static String rootRels() {
        return XML_DECL + "<Relationships xmlns=\"" + NS_PKG_REL + "\">"
                + "<Relationship Id=\"rId1\" Type=\"" + REL_TYPE
                + "officeDocument\" Target=\"word/document.xml\"/>"
                + "<Relationship Id=\"rId2\" Type=\"" + REL_TYPE
                + "extended-properties\" Target=\"docProps/app.xml\"/></Relationships>";
    }

    private static String documentRels() {
        return XML_DECL + "<Relationships xmlns=\"" + NS_PKG_REL + "\">"
                + "<Relationship Id=\"rId1\" Type=\"" + REL_TYPE
                + "styles\" Target=\"styles.xml\"/></Relationships>";
    }

    private static String appProps() {
        return XML_DECL + "<Properties xmlns=\"http://schemas.openxmlformats.org/officeDocument/2006/"
                + "extended-properties\"><Application>MJY Platform</Application></Properties>";
    }

    private static String styles() {
        return XML_DECL + "<w:styles xmlns:w=\"" + NS_W + "\">"
                + "<w:docDefaults><w:rPrDefault><w:rPr><w:sz w:val=\"21\"/></w:rPr></w:rPrDefault>"
                + "<w:pPrDefault/></w:docDefaults>"
                + "<w:style w:type=\"paragraph\" w:default=\"1\" w:styleId=\"Normal\">"
                + "<w:name w:val=\"Normal\"/></w:style>"
                + "<w:style w:type=\"paragraph\" w:styleId=\"Heading1\"><w:name w:val=\"heading 1\"/>"
                + "<w:pPr><w:outlineLvl w:val=\"0\"/></w:pPr><w:rPr><w:b/><w:sz w:val=\"32\"/></w:rPr></w:style>"
                + "<w:style w:type=\"paragraph\" w:styleId=\"Heading2\"><w:name w:val=\"heading 2\"/>"
                + "<w:pPr><w:outlineLvl w:val=\"1\"/></w:pPr><w:rPr><w:b/><w:sz w:val=\"26\"/></w:rPr></w:style>"
                + "</w:styles>";
    }

    private static final class ColumnStatistics {
        private final Map<String, Long> frequencies = new LinkedHashMap<>();
        private long missing;
        private long other;

        private void accept(String value) {
            if (value == null || value.isBlank()) {
                missing++;
                return;
            }
            Long count = frequencies.get(value);
            if (count != null) {
                frequencies.put(value, count + 1);
            } else if (frequencies.size() < MAX_DISTINCT_VALUES) {
                frequencies.put(value, 1L);
            } else {
                other++;
            }
        }
    }

    private record Statistics(long total, int answerStart, List<ColumnStatistics> columns) {
    }
}
