package cn.mjy.platform.response;

import static org.assertj.core.api.Assertions.assertThat;

import cn.mjy.platform.response.FieldDictionary.OptionLabel;
import java.io.ByteArrayInputStream;
import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.util.Arrays;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.zip.ZipEntry;
import java.util.zip.ZipInputStream;
import org.junit.jupiter.api.Test;

/**
 * 纯单元：SPSS 系统文件（.sav）导出（R06-03）。包里是 responses.sav 加三张 CSV 附表；
 * .sav 自带变量字典（变量标签与值标签），全部用 {@link SavReader} 按格式规范读回核对。
 */
class SavExportFormatTest {

    private static final String FORMULA = "=cmd|' /C calc'!A0";
    /** 259 字节，超过 SPSS 字符串变量 255 字节上限，且第 255 个字节落在一个汉字中间。 */
    private static final String LONG_TEXT = "x" + "汉".repeat(86);
    /** 截断只能停在字符边界：'x' 之后还剩 254 字节，只放得下 84 个汉字。 */
    private static final String TRUNCATED = "x" + "汉".repeat(84);
    /** 列代码里的 {@code [ ] #} 不是合法的 SPSS 变量名字符，只能换成下划线；原代码留在 variables.csv。 */
    private static final String MATRIX_CODE = "Q5[a_b]#1";
    private static final String MATRIX_NAME = "Q5_a_b__1";

    private static final List<ExportVariable> VARIABLES = List.of(
            new ExportVariable("version", "版本", List.of()),
            new ExportVariable("responseid", "答卷号", List.of()),
            new ExportVariable("state", "状态", List.of(new OptionLabel("in_progress", "进行中"),
                    new OptionLabel("engine_completed", "已完成"))),
            new ExportVariable("QSINGLE", "满意度", List.of(new OptionLabel("1", "很满意"),
                    new OptionLabel("2", "一般"))),
            new ExportVariable(MATRIX_CODE, "矩阵 [行 / 列]", List.of()),
            new ExportVariable("QLONG", "长文本", List.of()));

    private static final List<List<String>> ROWS = List.of(
            List.of("version", "responseid", "state", "QSINGLE", MATRIX_CODE, "QLONG"),
            List.of("版本", "答卷号", "状态", "满意度", "矩阵 [行 / 列]", "长文本"),
            Arrays.asList("1", "7", "engine_completed", "1", FORMULA, LONG_TEXT),
            Arrays.asList("1", "8", "in_progress", "2", "", null),
            Arrays.asList("2", "9", "engine_completed", null, "普通文本", "尾"));

    private static List<ExportSheet> bundle() {
        return List.of(
                new ExportSheet("responses", "答卷", 2, VARIABLES, sink -> {
                    for (List<String> row : ROWS) {
                        sink.accept(row);
                    }
                }),
                ExportSheet.of("fields", "字段字典", 1,
                        sink -> sink.accept(List.of("column", "label"))),
                ExportSheet.of("attachments", "附件清单", 1,
                        sink -> sink.accept(List.of("responseid", "name"))));
    }

    private static Map<String, byte[]> write() throws IOException {
        ByteArrayOutputStream out = new ByteArrayOutputStream();
        ExportFormat.SAV.writer().write(bundle(), out);
        Map<String, byte[]> entries = new LinkedHashMap<>();
        try (ZipInputStream in = new ZipInputStream(new ByteArrayInputStream(out.toByteArray()))) {
            ZipEntry entry;
            while ((entry = in.getNextEntry()) != null) {
                entries.put(entry.getName(), in.readAllBytes());
            }
        }
        return entries;
    }

    private static SavReader.Sav dataset() throws IOException {
        return SavReader.read(write().get("responses.sav"));
    }

    @Test
    void theBundleCarriesTheDatasetItsVariableDictionaryAndTheSideSheets() throws IOException {
        Map<String, byte[]> entries = write();

        assertThat(entries.keySet())
                .containsExactly("responses.sav", "variables.csv", "fields.csv", "attachments.csv");
        assertThat(entries.get("variables.csv")).startsWith(0xEF, 0xBB, 0xBF);
        List<List<String>> variables = csv(entries.get("variables.csv"));
        assertThat(variables.get(0)).containsExactly("name", "code", "label", "measure", "width", "valuelabels");
        assertThat(variables).hasSize(VARIABLES.size() + 1);
        assertThat(variables.get(4))
                .containsExactly("QSINGLE", "QSINGLE", "满意度", "numeric", "8", "1=很满意; 2=一般");
        assertThat(variables.get(5))
                .containsExactly(MATRIX_NAME, MATRIX_CODE, "矩阵 [行 / 列]", "string", "19", "");
    }

    @Test
    void theHeaderDescribesAnUncompressedUtf8DatasetWithOneCasePerDataRow() throws IOException {
        SavReader.Sav sav = dataset();

        assertThat(sav.layoutCode()).isEqualTo(2);
        assertThat(sav.compression()).isZero();
        assertThat(sav.encoding()).isEqualTo("UTF-8");
        assertThat(sav.caseCount()).isEqualTo(3);
        assertThat(sav.cases()).hasSize(3);
        // 三个数值变量各占 1 个 8 字节单元；状态列 16 字节（2 段）、矩阵列 19 字节（3 段）、长文本 255 字节（32 段）。
        assertThat(sav.nominalCaseSize()).isEqualTo(3 + 2 + 3 + 32);
    }

    @Test
    void everyColumnBecomesAVariableWhoseLongNameIsItsColumnCodeAndWhoseLabelIsTheChineseHeader()
            throws IOException {
        SavReader.Sav sav = dataset();

        assertThat(sav.variables()).extracting(SavReader.Variable::shortName)
                .containsExactly("V1", "V2", "V3", "V4", "V5", "V6");
        assertThat(sav.longNames()).containsExactly(Map.entry("V1", "version"), Map.entry("V2", "responseid"),
                Map.entry("V3", "state"), Map.entry("V4", "QSINGLE"), Map.entry("V5", MATRIX_NAME),
                Map.entry("V6", "QLONG"));
        assertThat(sav.variables()).extracting(SavReader.Variable::label)
                .containsExactly("版本", "答卷号", "状态", "满意度", "矩阵 [行 / 列]", "长文本");
    }

    @Test
    void allNumericColumnsBecomeNumericVariablesAndEverythingElseStaysText() throws IOException {
        SavReader.Sav sav = dataset();

        assertThat(sav.variable("V1").type()).isZero();
        assertThat(sav.variable("V2").type()).isZero();
        assertThat(sav.variable("V4").type()).as("选项代码与取值都是数字").isZero();
        assertThat(sav.variable("V3").type()).isEqualTo("engine_completed".length());
        assertThat(sav.variable("V5").type()).isEqualTo(("'" + FORMULA).getBytes(StandardCharsets.UTF_8).length);
        assertThat(sav.variable("V6").type()).as("超过 255 字节的列按上限定宽").isEqualTo(255);
    }

    @Test
    void valueLabelsAreAttachedToBothNumericAndShortStringVariables() throws IOException {
        SavReader.Sav sav = dataset();

        assertThat(sav.variable("V4").valueLabels()).containsExactly(
                new SavReader.ValueLabel(1.0d, "很满意"), new SavReader.ValueLabel(2.0d, "一般"));
        assertThat(sav.variable("V3").valueLabels()).as("宽于 8 字节的字符串变量挂不了值标签").isEmpty();
        assertThat(sav.variable("V6").valueLabels()).isEmpty();
    }

    @Test
    void cellsRoundTripWithSystemMissingForBlanksAndUtf8SafeTruncation() throws IOException {
        SavReader.Sav sav = dataset();

        assertThat(sav.cell(0, "version")).isEqualTo(1.0d);
        assertThat(sav.cell(0, "responseid")).isEqualTo(7.0d);
        assertThat(sav.cell(1, "QSINGLE")).isEqualTo(2.0d);
        assertThat(sav.cell(2, "QSINGLE")).as("空值是系统缺失，不是 0").isNull();
        assertThat(sav.cell(0, "state")).isEqualTo("engine_completed");
        assertThat(sav.cell(2, MATRIX_NAME)).isEqualTo("普通文本");
        assertThat(sav.cell(1, "QLONG")).as("null 单元格读回空串").isEqualTo("");
        assertThat(sav.cell(0, "QLONG")).as("255 字节上限按字符边界截断").isEqualTo(TRUNCATED);
    }

    @Test
    void formulaLookingTextIsGuardedInTheSavJustLikeInTheCsv() throws IOException {
        assertThat(dataset().cell(0, MATRIX_NAME)).isEqualTo("'" + FORMULA);
    }

    @Test
    void theSameInputProducesAByteIdenticalBundle() throws IOException {
        ByteArrayOutputStream first = new ByteArrayOutputStream();
        ByteArrayOutputStream second = new ByteArrayOutputStream();
        ExportFormat.SAV.writer().write(bundle(), first);
        ExportFormat.SAV.writer().write(bundle(), second);

        assertThat(first.toByteArray()).isEqualTo(second.toByteArray());
    }

    /** 留一份样例在 target/ 下，便于用 SPSS / PSPP / pyreadstat 人工核对。 */
    @Test
    void aSampleDatasetIsLeftInTargetForExternalReaders() throws IOException {
        java.nio.file.Path sample = java.nio.file.Path.of("target", "export-sample.sav");
        java.nio.file.Files.createDirectories(sample.getParent());
        java.nio.file.Files.write(sample, write().get("responses.sav"));

        assertThat(sample).exists();
    }

    private static List<List<String>> csv(byte[] bytes) {
        return ExportFixture.parseCsv(new String(bytes, 3, bytes.length - 3, StandardCharsets.UTF_8));
    }
}
