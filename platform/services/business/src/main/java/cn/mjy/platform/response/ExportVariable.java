package cn.mjy.platform.response;

import cn.mjy.platform.response.FieldDictionary.OptionLabel;
import java.util.List;
import java.util.Objects;

/**
 * 答卷表里的一列在统计格式里的变量描述：列代码、中文标签、可取值。
 *
 * <p>CSV 与 XLSX 把这些信息写在表头两行和字段字典表里；SPSS 的 SAV 把它们写进文件自带的变量字典
 * （变量标签与值标签，R06-03），因此写出方需要能按列拿到，而不只是拿到两行表头文本。
 *
 * @param code    列代码（与表头第一行一致）
 * @param label   中文标签（与表头第二行一致）
 * @param options 该列可取的选项代码与文本；自由文本列为空
 */
record ExportVariable(String code, String label, List<OptionLabel> options) {

    ExportVariable {
        Objects.requireNonNull(code, "code");
        Objects.requireNonNull(label, "label");
        options = List.copyOf(options);
    }
}
