package cn.mjy.platform.response;

import cn.mjy.platform.response.FieldDictionary.OptionLabel;
import java.nio.charset.StandardCharsets;
import java.util.List;

/**
 * SAV 数据集里的一个变量。宽度由扫描一遍数据得出（见 {@link SavDictionary}），因为 SPSS 的字典写在
 * 数据之前，必须先知道每一列最宽的取值。
 *
 * @param shortName 8 字节以内的旧式短名（{@code V1}…{@code Vn}），只在字典记录里出现
 * @param name      SPSS 变量名：由列代码消毒而来，只含字母数字与下划线
 * @param code      原始列代码（带方括号、井号的那种），只出现在 variables.csv 里
 * @param label     变量标签（中文表头）
 * @param width     字符串变量的字节宽度（1–255）；{@code 0} 表示数值变量
 * @param options   该列可取的选项代码与文本
 */
record SavVariable(String shortName, String name, String code, String label, int width, List<OptionLabel> options) {

    /** SPSS 字符串变量的单段上限；再长要用"超长字符串"扩展记录，本切片不做，超出的值截断。 */
    static final int MAX_STRING_BYTES = 255;
    /** 值标签只能挂在数值变量或不宽于 8 字节的短字符串变量上。 */
    static final int VALUE_LABEL_MAX_WIDTH = 8;
    private static final int NUMERIC_FORMAT = (5 << 16) | (8 << 8) | 2;
    private static final int STRING_FORMAT_TYPE = 1;

    SavVariable {
        options = List.copyOf(options);
    }

    boolean numeric() {
        return width == 0;
    }

    /** 该变量在一条记录里占几个 8 字节单元。 */
    int octets() {
        return numeric() ? 1 : (width + 7) / 8;
    }

    /** 打印 / 写出格式码：数值为 F8.2，字符串为 A&lt;宽度&gt;。 */
    int format() {
        return numeric() ? NUMERIC_FORMAT : (STRING_FORMAT_TYPE << 16) | (width << 8);
    }

    String measure() {
        return numeric() ? "numeric" : "string";
    }

    /** variables.csv 里写的宽度：数值变量占一个双精度，即 8 字节。 */
    int reportedWidth() {
        return numeric() ? Long.BYTES : width;
    }

    /**
     * 能挂上值标签的选项；挂不上的（长字符串列、超过 8 字节的代码）一个都不写，
     * 免得字典里只有一半的取值有标签。完整选项表始终在 variables.csv 里。
     */
    List<OptionLabel> labelledOptions() {
        if (options.isEmpty()) {
            return List.of();
        }
        if (numeric()) {
            return options;
        }
        if (width > VALUE_LABEL_MAX_WIDTH) {
            return List.of();
        }
        boolean allFit = options.stream()
                .allMatch(o -> o.code().getBytes(StandardCharsets.UTF_8).length <= VALUE_LABEL_MAX_WIDTH);
        return allFit ? options : List.of();
    }
}
