package cn.mjy.platform.response;

import java.util.regex.Pattern;

/**
 * 单元格文本的统一出口：防公式注入（R06-02）。
 *
 * <p>以 {@code = + - @}、制表符或回车开头、且不是纯数字的值前加一个单引号，表格软件打开时按文本显示
 * 而不执行。纯数字（含负数、小数、科学计数）保持原样。
 *
 * <p>每一种导出格式都从这里取值，包括二进制的统计格式：SPSS 自己不执行公式，但它能把数据集另存为
 * xlsx / csv，那一步又回到会执行公式的地方，所以防护统一在最外层做一次，不按格式开口子。
 */
final class ExportCellGuard {

    /** 纯数字：这些值即使以 {@code + -} 开头也不加引号。 */
    static final Pattern NUMBER = Pattern.compile("[+-]?(\\d+(\\.\\d*)?|\\.\\d+)([eE][+-]?\\d+)?");

    private static final String FORMULA_PREFIXES = "=+-@\t\r";

    private ExportCellGuard() {
    }

    /** @return 可以安全写进任何表格文件的文本；{@code null} 与空串都得到空串 */
    static String guard(String value) {
        if (value == null || value.isEmpty()) {
            return "";
        }
        if (FORMULA_PREFIXES.indexOf(value.charAt(0)) >= 0 && !NUMBER.matcher(value).matches()) {
            return "'" + value;
        }
        return value;
    }
}
