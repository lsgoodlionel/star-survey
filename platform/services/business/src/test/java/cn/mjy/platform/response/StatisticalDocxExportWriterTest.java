package cn.mjy.platform.response;

import static org.assertj.core.api.Assertions.assertThat;

import java.io.ByteArrayInputStream;
import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.List;
import java.util.zip.ZipInputStream;
import org.junit.jupiter.api.Test;

class StatisticalDocxExportWriterTest {

    @Test
    void writesFrequencyPercentAndMissingCountsForEveryAnswerField() throws IOException {
        ExportContent content = content(List.of(
                row("1", "满意", "北京"),
                row("2", "满意", "上海"),
                row("3", "不满意", null),
                row("4", null, "北京")));

        String document = document(write(content));

        assertThat(document).contains(
                "答卷统计报告",
                "答卷总数", "4",
                "满意度", "有效值", "3", "空值", "1",
                "满意", "2", "66.67%",
                "不满意", "1", "33.33%",
                "城市", "北京", "2", "66.67%", "上海", "1", "33.33%");
        assertThat(document).doesNotContain("答卷号", "开始时间");
    }

    @Test
    void capsDistinctValuesAndGroupsOverflowAsOther() throws IOException {
        List<List<String>> rows = new ArrayList<>();
        for (int i = 0; i < StatisticalDocxExportWriter.MAX_DISTINCT_VALUES + 7; i++) {
            rows.add(row(Integer.toString(i + 1), "值-" + i, "城市"));
        }

        String document = document(write(content(rows)));

        assertThat(document).contains("其他", "7", "6.54%");
        assertThat(document).doesNotContain("值-100", "值-106");
    }

    @Test
    void identicalInputProducesByteIdenticalDocx() throws IOException {
        ExportContent content = content(List.of(row("1", "满意", "北京")));
        assertThat(write(content)).isEqualTo(write(content));
    }

    private static ExportContent content(List<List<String>> rows) {
        List<String> codes = List.of("version", "sid", "responseid", "generation", "state", "startedat",
                "completedat", "answersstatus", "Q1", "Q2");
        List<String> labels = List.of("版本", "引擎问卷号", "答卷号", "代次", "状态", "开始时间", "完成时间",
                "作答状态", "满意度", "城市");
        return new ExportContent(List.of(), codes, labels, sink -> {
            for (List<String> row : rows) {
                sink.accept(new ExportRecord(row, List.of(), List.of()));
            }
        }, null);
    }

    private static List<String> row(String responseId, String answer, String city) {
        List<String> cells = new ArrayList<>(List.of(
                "1", "900001", responseId, "gen-a", "engine_completed",
                "2026-10-30T00:00:00Z", "2026-10-30T00:01:00Z", "available"));
        cells.add(answer);
        cells.add(city);
        return cells;
    }

    private static byte[] write(ExportContent content) throws IOException {
        ByteArrayOutputStream output = new ByteArrayOutputStream();
        new StatisticalDocxExportWriter().write(content, output);
        return output.toByteArray();
    }

    private static String document(byte[] docx) throws IOException {
        try (ZipInputStream zip = new ZipInputStream(new ByteArrayInputStream(docx))) {
            for (var entry = zip.getNextEntry(); entry != null; entry = zip.getNextEntry()) {
                if ("word/document.xml".equals(entry.getName())) {
                    return new String(zip.readAllBytes(), StandardCharsets.UTF_8);
                }
            }
        }
        throw new AssertionError("word/document.xml is missing");
    }
}
