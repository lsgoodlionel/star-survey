package cn.mjy.platform.response;

import cn.mjy.platform.engine.ResponseState;
import cn.mjy.platform.response.ExportPlan.PlannedSource;
import cn.mjy.platform.response.FieldDictionary.FieldEntry;
import cn.mjy.platform.response.FieldDictionary.OptionLabel;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.stream.Collectors;

/**
 * 由导出计划推出的表格布局：固定列＋各版本答卷列按"列代码"合并后的列序，以及三张表的表头。
 * 纯函数，同一计划永远得到同一布局。
 *
 * <p>列代码：单列题为题目代码；子题 / "其他" / 评论列为 {@code 代码[aid]}；双尺度第二尺度起再加 {@code #尺度}。
 * 同一版本内两列撞了列代码（理论上不会发生）时，后一列退回 {@code 列代码~引擎列名}，保证不丢列。
 */
final class ExportLayout {

    static final List<String> FIXED_CODES = List.of("version", "sid", "responseid", "generation", "state",
            "startedat", "completedat", "answersstatus");
    static final List<String> FIXED_LABELS = List.of("版本", "引擎问卷号", "答卷号", "代次", "状态", "开始时间",
            "完成时间", "作答状态");
    static final List<String> DICTIONARY_HEADER = List.of("version", "sid", "column", "fieldname", "questioncode",
            "type", "aid", "scale", "label", "sensitive", "masking", "options");
    static final List<String> ATTACHMENT_HEADER = List.of("version", "sid", "responseid", "column", "fieldname",
            "index", "name", "size", "ext", "storedname", "sha256");

    /** 固定列里可枚举的两列：投影状态与作答状态。统计格式据此写值标签。 */
    private static final Map<String, List<OptionLabel>> FIXED_OPTIONS = Map.of(
            "state", List.of(new OptionLabel(ResponseState.IN_PROGRESS.dbValue(), "进行中"),
                    new OptionLabel(ResponseState.ENGINE_COMPLETED.dbValue(), "已完成"),
                    new OptionLabel(ResponseState.DELETED.dbValue(), "已删除")),
            "answersstatus", List.of(new OptionLabel(AnswersStatus.AVAILABLE.wire(), "已取到作答"),
                    new OptionLabel(AnswersStatus.DELETED.wire(), "答卷已删除"),
                    new OptionLabel(AnswersStatus.ARCHIVED.wire(), "已被替换的代次"),
                    new OptionLabel(AnswersStatus.MISSING.wire(), "引擎当前表里没有")));

    /** 合并后的一列：代码、标签与可取值（都取最早定义它的版本）。 */
    record Column(String code, String label, List<OptionLabel> options) {

        Column {
            options = List.copyOf(options);
        }
    }

    private final ExportPlan plan;
    private final List<Column> columns;
    /** 版本号 → (引擎列名 → 列序号，从 0 起，不含固定列)。 */
    private final Map<Integer, Map<String, Integer>> positions;

    ExportLayout(ExportPlan plan) {
        this.plan = plan;
        Map<String, Integer> byCode = new LinkedHashMap<>();
        List<Column> merged = new ArrayList<>();
        Map<Integer, Map<String, Integer>> perVersion = new HashMap<>();
        for (PlannedSource source : plan.sources()) {
            Map<String, Integer> mine = new LinkedHashMap<>();
            for (FieldEntry field : source.fields()) {
                if (mine.containsKey(field.fieldname())) {
                    continue;
                }
                String code = columnCode(field);
                if (mine.containsValue(byCode.get(code))) {
                    code = code + "~" + field.fieldname();
                }
                Integer index = byCode.get(code);
                if (index == null) {
                    index = merged.size();
                    byCode.put(code, index);
                    merged.add(new Column(code, field.label(), field.options()));
                }
                mine.put(field.fieldname(), index);
            }
            perVersion.put(source.version(), Map.copyOf(mine));
        }
        this.columns = List.copyOf(merged);
        this.positions = Map.copyOf(perVersion);
    }

    static String columnCode(FieldEntry field) {
        String code = field.aid().isEmpty() ? field.code() : field.code() + "[" + field.aid() + "]";
        return field.scale() > 0 ? code + "#" + field.scale() : code;
    }

    ExportPlan plan() {
        return plan;
    }

    List<Column> columns() {
        return columns;
    }

    int width() {
        return FIXED_CODES.size() + columns.size();
    }

    Map<String, Integer> positions(int version) {
        return positions.getOrDefault(version, Map.of());
    }

    /** 答卷表每一列的变量描述，顺序与表头一致（固定列在前）。 */
    List<ExportVariable> variables() {
        List<ExportVariable> variables = new ArrayList<>(width());
        for (int i = 0; i < FIXED_CODES.size(); i++) {
            String code = FIXED_CODES.get(i);
            variables.add(new ExportVariable(code, FIXED_LABELS.get(i), FIXED_OPTIONS.getOrDefault(code, List.of())));
        }
        columns.forEach(c -> variables.add(new ExportVariable(c.code(), c.label(), c.options())));
        return List.copyOf(variables);
    }

    List<List<String>> responseHeader() {
        List<String> codes = new ArrayList<>(FIXED_CODES);
        List<String> labels = new ArrayList<>(FIXED_LABELS);
        columns.forEach(c -> {
            codes.add(c.code());
            labels.add(c.label());
        });
        return List.of(List.copyOf(codes), List.copyOf(labels));
    }

    /** 数据字典：每个版本每一列一行，标明敏感与否以及本文件里是否已遮蔽。 */
    List<List<String>> dictionaryRows(boolean revealSensitive) {
        List<List<String>> rows = new ArrayList<>();
        rows.add(DICTIONARY_HEADER);
        for (PlannedSource source : plan.sources()) {
            Map<String, Integer> mine = positions(source.version());
            for (FieldEntry field : source.fields()) {
                Integer index = mine.get(field.fieldname());
                String masking = !field.sensitive() ? "" : revealSensitive ? "plaintext" : "masked";
                rows.add(List.of(Integer.toString(source.version()), Long.toString(source.engineSid()),
                        index == null ? "" : columns.get(index).code(), field.fieldname(), field.code(),
                        field.type(), field.aid(), Integer.toString(field.scale()), field.label(),
                        Boolean.toString(field.sensitive()), masking, options(field.options())));
            }
        }
        return List.copyOf(rows);
    }

    private static String options(List<OptionLabel> options) {
        return options.stream().map(o -> o.code() + "=" + o.text()).collect(Collectors.joining("; "));
    }
}
