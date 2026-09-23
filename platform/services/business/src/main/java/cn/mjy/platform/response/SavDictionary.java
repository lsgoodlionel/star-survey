package cn.mjy.platform.response;

import cn.mjy.platform.response.FieldDictionary.OptionLabel;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.HashSet;
import java.util.List;
import java.util.Locale;
import java.util.Set;

/**
 * 扫一遍答卷表得出的 SAV 字典：每列是数值还是字符串、字符串有多宽、一共多少条记录。
 *
 * <p>为什么要先扫一遍：SPSS 的字典写在数据之前，而变量宽度只能由最宽的取值决定。行是从分片里流式重放的
 * （{@link ExportSheet.RowSource} 可以重复调用），扫描不把任何一行留在内存里，因此内存占用与答卷数无关，
 * 代价是分片多读一遍。
 *
 * @param caseCount 数据行数（不含表头）
 */
record SavDictionary(List<SavVariable> variables, int caseCount) {

    /** 超过这个位数的整数放进双精度会悄悄丢精度，这种列宁可当文本。 */
    private static final int MAX_SIGNIFICANT_DIGITS = 15;
    private static final int MAX_NAME_BYTES = 64;
    private static final String FALLBACK_NAME_PREFIX = "V";

    SavDictionary {
        variables = List.copyOf(variables);
    }

    /** 一条记录占几个 8 字节单元（SPSS 文件头的 nominal_case_size）。 */
    int nominalCaseSize() {
        return variables.stream().mapToInt(SavVariable::octets).sum();
    }

    static SavDictionary of(ExportSheet sheet) throws IOException {
        List<ExportVariable> columns = sheet.variables();
        int width = columns.size();
        boolean[] numeric = new boolean[width];
        boolean[] seen = new boolean[width];
        int[] bytes = new int[width];
        java.util.Arrays.fill(numeric, true);
        int[] counted = {0};
        int[] remainingHeader = {sheet.headerRows()};

        sheet.rows().forEach(cells -> {
            if (remainingHeader[0] > 0) {
                remainingHeader[0]--;
                return;
            }
            counted[0]++;
            for (int i = 0; i < width; i++) {
                String value = ExportCellGuard.guard(i < cells.size() ? cells.get(i) : null);
                if (value.isEmpty()) {
                    continue;
                }
                seen[i] = true;
                bytes[i] = Math.max(bytes[i], Math.min(SavVariable.MAX_STRING_BYTES,
                        value.getBytes(StandardCharsets.UTF_8).length));
                numeric[i] = numeric[i] && isSafeNumber(value);
            }
        });

        List<SavVariable> variables = new ArrayList<>(width);
        Set<String> taken = new HashSet<>();
        for (int i = 0; i < width; i++) {
            ExportVariable column = columns.get(i);
            boolean asNumber = seen[i] && numeric[i] && numericOptions(column.options());
            variables.add(new SavVariable(FALLBACK_NAME_PREFIX + (i + 1), uniqueName(column.code(), taken),
                    column.code(), column.label(), asNumber ? 0 : stringWidth(bytes[i], column.options()),
                    column.options()));
        }
        return new SavDictionary(variables, counted[0]);
    }

    /**
     * 字符串变量的宽度：至少放得下观察到的最宽取值，也至少放得下最长的选项代码——SPSS 比对值标签时
     * 只看变量宽度那几个字节，变量比代码窄就会把两个不同的选项认成同一个。
     */
    private static int stringWidth(int observedBytes, List<OptionLabel> options) {
        int widest = options.stream()
                .mapToInt(option -> option.code().getBytes(StandardCharsets.UTF_8).length)
                .max().orElse(0);
        return Math.min(SavVariable.MAX_STRING_BYTES, Math.max(1, Math.max(observedBytes, widest)));
    }

    /** 选项代码不全是数字时整列按文本导出，免得值标签与取值对不上。 */
    private static boolean numericOptions(List<OptionLabel> options) {
        return options.stream().allMatch(option -> isSafeNumber(option.code()));
    }

    /**
     * 能不能当数值：形状是数字，位数不超过双精度能精确表示的范围，且没有前导零
     * （{@code 007} 是代码不是数量，变成 {@code 7} 就再也对不回去了）。
     */
    static boolean isSafeNumber(String value) {
        if (value == null || value.isEmpty() || !ExportCellGuard.NUMBER.matcher(value).matches()) {
            return false;
        }
        String unsigned = value.charAt(0) == '+' || value.charAt(0) == '-' ? value.substring(1) : value;
        if (unsigned.length() > 1 && unsigned.charAt(0) == '0' && Character.isDigit(unsigned.charAt(1))) {
            return false;
        }
        long digits = unsigned.chars().filter(Character::isDigit).count();
        return digits > 0 && digits <= MAX_SIGNIFICANT_DIGITS;
    }

    /**
     * 列代码 → SPSS 变量名：只留字母数字与下划线（列代码里的 {@code [ ] # ~} 都不是合法名字字符），
     * 不能以数字开头，最长 64 字节，忽略大小写去重。原始列代码另外写进 variables.csv，不会丢。
     */
    static String uniqueName(String code, Set<String> taken) {
        StringBuilder safe = new StringBuilder(code.length());
        for (int i = 0; i < code.length(); i++) {
            char c = code.charAt(i);
            safe.append(c < 128 && (Character.isLetterOrDigit(c) || c == '_') ? c : '_');
        }
        if (safe.isEmpty() || Character.isDigit(safe.charAt(0))) {
            safe.insert(0, FALLBACK_NAME_PREFIX);
        }
        if (safe.length() > MAX_NAME_BYTES) {
            safe.setLength(MAX_NAME_BYTES);
        }
        String base = safe.toString();
        String candidate = base;
        for (int suffix = 2; !taken.add(candidate.toLowerCase(Locale.ROOT)); suffix++) {
            String tail = "_" + suffix;
            int keep = Math.min(base.length(), MAX_NAME_BYTES - tail.length());
            candidate = base.substring(0, keep) + tail;
        }
        return candidate;
    }
}
