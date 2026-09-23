package cn.mjy.platform.response;

import cn.mjy.platform.response.FieldDictionary.OptionLabel;
import java.io.IOException;
import java.io.OutputStream;
import java.nio.charset.StandardCharsets;
import java.util.List;

/**
 * 只用 JDK 写出的 SPSS 系统文件（.sav，R06-03）：小端序、未压缩，按格式规范逐条写记录——
 * 文件头、变量记录（含续记录）、值标签记录、扩展记录（长变量名、字符编码、机器信息）、字典结束记录、数据区。
 *
 * <p>确定性：创建日期与时间是<b>常量</b>而不是当下时刻，和 ZIP 条目时间一样，为的是同样的数据得到
 * 逐字节相同的文件（崩溃恢复后重跑的文件必须与一次跑完的相同，ADR 0015 决定 5）。
 *
 * <p>本切片不做的：数据压缩、超长字符串（&gt; 255 字节按字符边界截断）、用户自定义缺失值。
 */
final class SavFile {

    static final int LAYOUT_CODE = 2;
    static final int NO_COMPRESSION = 0;
    /** SPSS 的系统缺失值。 */
    static final double SYSMIS = -Double.MAX_VALUE;
    /** SPSS 变量标签与值标签的长度上限（字节）。 */
    static final int MAX_VARIABLE_LABEL_BYTES = 255;
    static final int MAX_VALUE_LABEL_BYTES = 120;

    private static final String MAGIC = "$FL2";
    private static final String PRODUCT = "@(#) SPSS DATA FILE MJY Platform";
    private static final String FIXED_DATE = "01 Jan 80";
    private static final String FIXED_TIME = "00:00:00";
    private static final double BIAS = 100.0d;
    private static final int SHORT_NAME_BYTES = 8;
    private static final int OCTET = 8;
    private static final byte PAD = ' ';

    private static final int REC_VARIABLE = 2;
    private static final int REC_VALUE_LABELS = 3;
    private static final int REC_LABEL_TARGETS = 4;
    private static final int REC_EXTENSION = 7;
    private static final int REC_DICTIONARY_END = 999;
    private static final int SUB_MACHINE_INTEGER = 3;
    private static final int SUB_MACHINE_FLOAT = 4;
    private static final int SUB_LONG_NAMES = 13;
    private static final int SUB_ENCODING = 20;
    private static final int LITTLE_ENDIAN = 2;
    private static final int UTF8_CODE_PAGE = 65001;

    private SavFile() {
    }

    /**
     * 按字典把答卷表写成一个 .sav。行会被重放一遍（字典是上一遍扫出来的），任何时刻只持有一行。
     *
     * @throws IOException 重放出来的行数与字典对不上时也抛——宁可失败，不写出字典与数据不一致的文件
     */
    static void write(ExportSheet sheet, SavDictionary dictionary, OutputStream out) throws IOException {
        if (dictionary.variables().isEmpty()) {
            throw new IOException("a sav dataset needs at least one variable");
        }
        header(out, dictionary);
        for (SavVariable variable : dictionary.variables()) {
            variableRecords(out, variable);
        }
        writeValueLabels(out, dictionary);
        extensions(out, dictionary);
        int32(out, REC_DICTIONARY_END);
        int32(out, 0);
        data(out, sheet, dictionary);
    }

    private static void header(OutputStream out, SavDictionary dictionary) throws IOException {
        ascii(out, MAGIC, MAGIC.length());
        ascii(out, PRODUCT, 60);
        int32(out, LAYOUT_CODE);
        int32(out, dictionary.nominalCaseSize());
        int32(out, NO_COMPRESSION);
        int32(out, 0);
        int32(out, dictionary.caseCount());
        float64(out, BIAS);
        ascii(out, FIXED_DATE, 9);
        ascii(out, FIXED_TIME, 8);
        ascii(out, "", 64);
        out.write(new byte[3]);
    }

    /** 一条主记录，加上字符串变量每个额外 8 字节段的一条续记录。 */
    private static void variableRecords(OutputStream out, SavVariable variable) throws IOException {
        byte[] label = truncate(variable.label(), MAX_VARIABLE_LABEL_BYTES);
        int32(out, REC_VARIABLE);
        int32(out, variable.numeric() ? 0 : variable.width());
        int32(out, label.length == 0 ? 0 : 1);
        int32(out, 0);
        int32(out, variable.format());
        int32(out, variable.format());
        ascii(out, variable.shortName(), SHORT_NAME_BYTES);
        if (label.length > 0) {
            int32(out, label.length);
            padded(out, label, roundUp(label.length, 4));
        }
        for (int segment = 1; segment < variable.octets(); segment++) {
            int32(out, REC_VARIABLE);
            int32(out, -1);
            int32(out, 0);
            int32(out, 0);
            int32(out, 0);
            int32(out, 0);
            ascii(out, "", SHORT_NAME_BYTES);
        }
    }

    /** 值标签：一条记录 3 列出取值与标签，紧跟一条记录 4 指出它属于哪个字典序号。 */
    private static void writeValueLabels(OutputStream out, SavDictionary dictionary) throws IOException {
        int dictionaryIndex = 1;
        for (SavVariable variable : dictionary.variables()) {
            List<OptionLabel> options = variable.labelledOptions();
            if (!options.isEmpty()) {
                int32(out, REC_VALUE_LABELS);
                int32(out, options.size());
                for (OptionLabel option : options) {
                    value(out, variable, option.code());
                    byte[] text = truncate(option.text(), MAX_VALUE_LABEL_BYTES);
                    out.write(text.length);
                    padded(out, text, roundUp(text.length + 1, OCTET) - 1);
                }
                int32(out, REC_LABEL_TARGETS);
                int32(out, 1);
                int32(out, dictionaryIndex);
            }
            dictionaryIndex += variable.octets();
        }
    }

    private static void value(OutputStream out, SavVariable variable, String code) throws IOException {
        if (variable.numeric()) {
            float64(out, Double.parseDouble(code));
        } else {
            padded(out, truncate(code, OCTET), OCTET);
        }
    }

    private static void extensions(OutputStream out, SavDictionary dictionary) throws IOException {
        extension(out, SUB_MACHINE_INTEGER, 4, new int[]{1, 0, 0, -1, 1, 1, LITTLE_ENDIAN, UTF8_CODE_PAGE});
        int32(out, REC_EXTENSION);
        int32(out, SUB_MACHINE_FLOAT);
        int32(out, OCTET);
        int32(out, 3);
        float64(out, SYSMIS);
        float64(out, Double.MAX_VALUE);
        float64(out, SYSMIS);

        StringBuilder names = new StringBuilder();
        for (SavVariable variable : dictionary.variables()) {
            if (!names.isEmpty()) {
                names.append('\t');
            }
            names.append(variable.shortName()).append('=').append(variable.name());
        }
        bytes(out, SUB_LONG_NAMES, names.toString().getBytes(StandardCharsets.UTF_8));
        bytes(out, SUB_ENCODING, "UTF-8".getBytes(StandardCharsets.UTF_8));
    }

    private static void extension(OutputStream out, int subtype, int size, int[] values) throws IOException {
        int32(out, REC_EXTENSION);
        int32(out, subtype);
        int32(out, size);
        int32(out, values.length);
        for (int value : values) {
            int32(out, value);
        }
    }

    private static void bytes(OutputStream out, int subtype, byte[] data) throws IOException {
        int32(out, REC_EXTENSION);
        int32(out, subtype);
        int32(out, 1);
        int32(out, data.length);
        out.write(data);
    }

    private static void data(OutputStream out, ExportSheet sheet, SavDictionary dictionary) throws IOException {
        List<SavVariable> variables = dictionary.variables();
        int[] remainingHeader = {sheet.headerRows()};
        int[] written = {0};
        sheet.rows().forEach(cells -> {
            if (remainingHeader[0] > 0) {
                remainingHeader[0]--;
                return;
            }
            written[0]++;
            for (int i = 0; i < variables.size(); i++) {
                SavVariable variable = variables.get(i);
                String value = ExportCellGuard.guard(i < cells.size() ? cells.get(i) : null);
                if (variable.numeric()) {
                    float64(out, value.isEmpty() ? SYSMIS : Double.parseDouble(value));
                } else {
                    padded(out, truncate(value, variable.width()), variable.octets() * OCTET);
                }
            }
        });
        if (written[0] != dictionary.caseCount()) {
            throw new IOException("sav data has " + written[0] + " cases but the dictionary declares "
                    + dictionary.caseCount());
        }
    }

    /** 按 UTF-8 截断到不超过 limit 字节，且不把一个字符劈成两半。 */
    static byte[] truncate(String value, int limit) {
        byte[] raw = value.getBytes(StandardCharsets.UTF_8);
        if (raw.length <= limit) {
            return raw;
        }
        int end = limit;
        while (end > 0 && (raw[end] & 0xC0) == 0x80) {
            end--;
        }
        byte[] cut = new byte[end];
        System.arraycopy(raw, 0, cut, 0, end);
        return cut;
    }

    private static void padded(OutputStream out, byte[] value, int length) throws IOException {
        out.write(value, 0, Math.min(value.length, length));
        for (int i = value.length; i < length; i++) {
            out.write(PAD);
        }
    }

    private static void ascii(OutputStream out, String value, int length) throws IOException {
        padded(out, value.getBytes(StandardCharsets.ISO_8859_1), length);
    }

    private static void int32(OutputStream out, int value) throws IOException {
        for (int i = 0; i < 4; i++) {
            out.write(value >>> (8 * i));
        }
    }

    private static void float64(OutputStream out, double value) throws IOException {
        long bits = Double.doubleToRawLongBits(value);
        for (int i = 0; i < OCTET; i++) {
            out.write((int) (bits >>> (8 * i)));
        }
    }

    private static int roundUp(int value, int multiple) {
        int remainder = value % multiple;
        return remainder == 0 ? value : value + multiple - remainder;
    }
}
