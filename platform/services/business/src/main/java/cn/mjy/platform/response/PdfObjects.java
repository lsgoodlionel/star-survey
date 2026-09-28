package cn.mjy.platform.response;

import java.io.IOException;
import java.io.OutputStream;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.List;
import java.util.zip.Deflater;

/**
 * PDF 的骨架：按序写对象、记住每个对象的字节偏移，最后写交叉引用表与 trailer。
 * 只往前写，不回头改——底层是导出作业的上传流，不可寻址。
 *
 * <p><b>逐字节可复现</b>（崩溃恢复后的文件必须与一次跑完的相同，见 {@link ResponseExportResumeTest}）：
 *
 * <ul>
 *   <li>不写 {@code /Info}，所以没有 {@code /CreationDate}、{@code /ModDate}——PDF 里破坏
 *       可复现性最常见的就是这两样；</li>
 *   <li>{@code /ID} 是常量而不是随机数或时间摘要；</li>
 *   <li>压缩用固定的 {@link Deflater} 级别，与 {@link ExportZip} 依赖的是同一个确定性。</li>
 * </ul>
 *
 * <p>内存有界：只留一份「对象号 → 偏移」的表（对象数与答卷份数同阶，上限 1 万份），
 * 不缓存任何对象内容。
 */
final class PdfObjects {

    private static final String VERSION = "%PDF-1.7\n";
    /** 规范建议紧跟一行高位字节注释，让传输工具把文件当二进制。 */
    private static final byte[] BINARY_MARKER = {'%', (byte) 0xE4, (byte) 0xBD, (byte) 0xA0, '\n'};
    /** 固定的文档 ID：内容一样的两份文件必须逐字节相同，所以这里不能有随机性。 */
    private static final String DOCUMENT_ID = "4d4a59504446303030303030303030303030303031";
    private static final int XREF_ENTRY = 20;

    private final OutputStream out;
    /** 下标即对象号；0 号是规范规定的空闲头。 */
    private final List<Long> offsets = new ArrayList<>(List.of(0L));
    private long position;

    PdfObjects(OutputStream out) {
        this.out = out;
    }

    void header() throws IOException {
        write(VERSION.getBytes(StandardCharsets.US_ASCII));
        write(BINARY_MARKER);
    }

    /** 预定一个对象号，稍后再写它的内容。前向引用在 PDF 里是常规做法。 */
    int reserve() {
        offsets.add(-1L);
        return offsets.size() - 1;
    }

    /** 写一个字典对象。 */
    void dictionary(int number, String body) throws IOException {
        begin(number);
        write(body.getBytes(StandardCharsets.US_ASCII));
        write("\nendobj\n".getBytes(StandardCharsets.US_ASCII));
    }

    /**
     * 写一个流对象；内容压缩后写出。
     *
     * @param extra 除 {@code /Length} 与 {@code /Filter} 之外要写进流字典的键值，可为空
     */
    void stream(int number, String extra, byte[] data) throws IOException {
        byte[] compressed = deflate(data);
        begin(number);
        write(("<< /Length " + compressed.length + " /Filter /FlateDecode" + extra + " >>\nstream\n")
                .getBytes(StandardCharsets.US_ASCII));
        write(compressed);
        write("\nendstream\nendobj\n".getBytes(StandardCharsets.US_ASCII));
    }

    private void begin(int number) throws IOException {
        if (number >= offsets.size()) {
            throw new IllegalArgumentException("object " + number + " was never reserved");
        }
        offsets.set(number, position);
        write((number + " 0 obj\n").getBytes(StandardCharsets.US_ASCII));
    }

    /** 收尾：交叉引用表、trailer、{@code startxref}、{@code %%EOF}。 */
    void trailer(int catalogNumber) throws IOException {
        for (int number = 1; number < offsets.size(); number++) {
            if (offsets.get(number) < 0) {
                throw new IllegalStateException("object " + number + " was reserved but never written");
            }
        }
        long startxref = position;
        StringBuilder xref = new StringBuilder("xref\n0 " + offsets.size() + "\n");
        xref.append("0000000000 65535 f \n");
        for (int number = 1; number < offsets.size(); number++) {
            xref.append(String.format(java.util.Locale.ROOT, "%010d 00000 n \n", offsets.get(number)));
        }
        if (xref.length() != "xref\n0 \n".length() + Integer.toString(offsets.size()).length()
                + XREF_ENTRY * offsets.size()) {
            throw new IllegalStateException("cross-reference entries must be exactly 20 bytes");
        }
        xref.append("trailer\n<< /Size ").append(offsets.size()).append(" /Root ").append(catalogNumber)
                .append(" 0 R /ID [<").append(DOCUMENT_ID).append("> <").append(DOCUMENT_ID).append(">] >>\n")
                .append("startxref\n").append(startxref).append("\n%%EOF\n");
        write(xref.toString().getBytes(StandardCharsets.US_ASCII));
    }

    private void write(byte[] bytes) throws IOException {
        out.write(bytes);
        position += bytes.length;
    }

    /** 固定级别的 Deflate：同样的输入得到同样的字节。 */
    private static byte[] deflate(byte[] data) {
        Deflater deflater = new Deflater(Deflater.DEFAULT_COMPRESSION);
        try {
            deflater.setInput(data);
            deflater.finish();
            byte[] buffer = new byte[Math.max(64, data.length / 2 + 64)];
            java.io.ByteArrayOutputStream compressed = new java.io.ByteArrayOutputStream(buffer.length);
            while (!deflater.finished()) {
                compressed.write(buffer, 0, deflater.deflate(buffer));
            }
            return compressed.toByteArray();
        } finally {
            deflater.end();
        }
    }
}
