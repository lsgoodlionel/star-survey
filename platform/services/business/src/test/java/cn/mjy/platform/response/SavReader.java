package cn.mjy.platform.response;

import java.io.ByteArrayInputStream;
import java.io.DataInputStream;
import java.io.EOFException;
import java.io.IOException;
import java.io.UncheckedIOException;
import java.nio.ByteBuffer;
import java.nio.ByteOrder;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * 测试用的最小 SPSS 系统文件（.sav）读回实现：按格式规范逐条记录解析，不依赖任何第三方库，
 * 也不复用被测代码的任何常量——写出方改了字节，这里就会读不出来。
 *
 * <p>只支持未压缩的数据区（导出写出的就是未压缩），只读需要核对的部分：文件头、变量记录、
 * 值标签记录、扩展记录（长变量名、字符编码）、字典结束记录与数据区。
 */
final class SavReader {

    /** SPSS 的系统缺失值。 */
    static final double SYSMIS = -Double.MAX_VALUE;

    record ValueLabel(Object value, String label) {
    }

    /**
     * @param type 0 为数值变量，大于 0 为字符串变量的字节宽度
     */
    record Variable(String shortName, String label, int type, int printFormat, List<ValueLabel> valueLabels) {
    }

    /**
     * @param cases 每个单元格：数值变量为 {@link Double}（系统缺失为 {@code null}），字符串变量为去掉尾部空格的文本
     */
    record Sav(int layoutCode, int compression, int caseCount, int nominalCaseSize, String encoding,
            String creationDate, String creationTime, List<Variable> variables, Map<String, String> longNames,
            List<List<Object>> cases) {

        Variable variable(String shortName) {
            return variables.stream().filter(v -> v.shortName().equals(shortName)).findFirst()
                    .orElseThrow(() -> new AssertionError("no variable " + shortName));
        }

        /** 长变量名（列代码）→ 短名，方便按列代码断言。 */
        String shortNameOf(String longName) {
            return longNames.entrySet().stream().filter(e -> e.getValue().equals(longName)).map(Map.Entry::getKey)
                    .findFirst().orElseThrow(() -> new AssertionError("no long name " + longName));
        }

        Object cell(int row, String longName) {
            return cases.get(row).get(variables.indexOf(variable(shortNameOf(longName))));
        }
    }

    private final DataInputStream in;
    private final List<Variable> variables = new ArrayList<>();
    /** 字典序号（含续记录）→ variables 里的下标；值标签记录按字典序号指变量。 */
    private final List<Integer> dictionaryIndex = new ArrayList<>();
    private final Map<String, String> longNames = new LinkedHashMap<>();
    private String encoding = "";

    private SavReader(byte[] bytes) {
        this.in = new DataInputStream(new ByteArrayInputStream(bytes));
    }

    static Sav read(byte[] bytes) {
        try {
            return new SavReader(bytes).parse();
        } catch (IOException e) {
            throw new UncheckedIOException(e);
        }
    }

    private Sav parse() throws IOException {
        if (!"$FL2".equals(ascii(4))) {
            throw new IOException("not a sav file");
        }
        ascii(60);
        int layoutCode = int32();
        int nominalCaseSize = int32();
        int compression = int32();
        int32();
        int caseCount = int32();
        float64();
        String creationDate = ascii(9);
        String creationTime = ascii(8);
        ascii(64);
        in.readNBytes(3);

        List<ValueLabel> pending = List.of();
        while (true) {
            int type = int32();
            if (type == 999) {
                int32();
                break;
            }
            switch (type) {
                case 2 -> variableRecord();
                case 3 -> pending = valueLabelRecord();
                case 4 -> {
                    attach(pending);
                    pending = List.of();
                }
                case 7 -> extensionRecord();
                default -> throw new IOException("unexpected record type " + type);
            }
        }
        return new Sav(layoutCode, compression, caseCount, nominalCaseSize, encoding, creationDate, creationTime,
                List.copyOf(variables), java.util.Collections.unmodifiableMap(new LinkedHashMap<>(longNames)),
                cases(caseCount));
    }

    private void variableRecord() throws IOException {
        int type = int32();
        int hasLabel = int32();
        int missingCount = int32();
        int print = int32();
        int32();
        String name = ascii(8).stripTrailing();
        String label = "";
        if (hasLabel == 1) {
            int length = int32();
            label = utf8(length, roundUp(length, 4));
        }
        if (missingCount != 0) {
            in.readNBytes(8 * Math.abs(missingCount));
        }
        if (type == -1) {
            dictionaryIndex.add(null);
            return;
        }
        dictionaryIndex.add(variables.size());
        variables.add(new Variable(name, label, type, print, new ArrayList<>()));
    }

    private List<ValueLabel> valueLabelRecord() throws IOException {
        int count = int32();
        List<byte[]> raw = new ArrayList<>();
        List<String> labels = new ArrayList<>();
        for (int i = 0; i < count; i++) {
            raw.add(in.readNBytes(8));
            int length = in.read();
            int padded = roundUp(length + 1, 8) - 1;
            byte[] text = in.readNBytes(padded);
            labels.add(new String(text, 0, length, StandardCharsets.UTF_8));
        }
        List<ValueLabel> pending = new ArrayList<>();
        for (int i = 0; i < count; i++) {
            pending.add(new ValueLabel(raw.get(i), labels.get(i)));
        }
        return pending;
    }

    /** 记录 4 给出这批值标签属于哪些变量；数值变量的 8 字节按 double 解，短字符串按文本解。 */
    private void attach(List<ValueLabel> pending) throws IOException {
        int count = int32();
        for (int i = 0; i < count; i++) {
            Integer target = dictionaryIndex.get(int32() - 1);
            if (target == null) {
                throw new IOException("value labels point at a continuation record");
            }
            Variable variable = variables.get(target);
            for (ValueLabel label : pending) {
                byte[] raw = (byte[]) label.value();
                Object value = variable.type() == 0
                        ? ByteBuffer.wrap(raw).order(ByteOrder.LITTLE_ENDIAN).getDouble()
                        : new String(raw, StandardCharsets.UTF_8).stripTrailing();
                variable.valueLabels().add(new ValueLabel(value, label.label()));
            }
        }
    }

    private void extensionRecord() throws IOException {
        int subtype = int32();
        int size = int32();
        int count = int32();
        byte[] data = in.readNBytes(size * count);
        if (subtype == 13) {
            for (String pair : new String(data, StandardCharsets.UTF_8).split("\t")) {
                int split = pair.indexOf('=');
                if (split > 0) {
                    longNames.put(pair.substring(0, split), pair.substring(split + 1));
                }
            }
        } else if (subtype == 20) {
            encoding = new String(data, StandardCharsets.UTF_8);
        }
    }

    private List<List<Object>> cases(int caseCount) throws IOException {
        List<List<Object>> cases = new ArrayList<>();
        for (int c = 0; c < caseCount; c++) {
            List<Object> row = new ArrayList<>();
            for (Variable variable : variables) {
                row.add(variable.type() == 0 ? numericCell() : stringCell(variable.type()));
            }
            cases.add(java.util.Collections.unmodifiableList(row));
        }
        if (in.read() != -1) {
            throw new IOException("trailing bytes after the last case");
        }
        return java.util.Collections.unmodifiableList(cases);
    }

    private Object numericCell() throws IOException {
        double value = float64();
        return value == SYSMIS ? null : value;
    }

    private String stringCell(int width) throws IOException {
        byte[] bytes = in.readNBytes(roundUp(width, 8));
        return new String(bytes, 0, width, StandardCharsets.UTF_8).stripTrailing();
    }

    private static int roundUp(int value, int multiple) {
        int remainder = value % multiple;
        return remainder == 0 ? value : value + multiple - remainder;
    }

    private String ascii(int length) throws IOException {
        byte[] bytes = in.readNBytes(length);
        if (bytes.length != length) {
            throw new EOFException("truncated sav file");
        }
        return new String(bytes, StandardCharsets.ISO_8859_1);
    }

    private String utf8(int length, int padded) throws IOException {
        byte[] bytes = in.readNBytes(padded);
        return new String(bytes, 0, length, StandardCharsets.UTF_8);
    }

    private int int32() throws IOException {
        return ByteBuffer.wrap(in.readNBytes(4)).order(ByteOrder.LITTLE_ENDIAN).getInt();
    }

    private double float64() throws IOException {
        return ByteBuffer.wrap(in.readNBytes(8)).order(ByteOrder.LITTLE_ENDIAN).getDouble();
    }
}
