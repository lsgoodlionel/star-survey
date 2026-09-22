package cn.mjy.platform.response;

import java.io.FilterOutputStream;
import java.io.IOException;
import java.io.OutputStream;
import java.time.LocalDateTime;
import java.util.zip.ZipEntry;
import java.util.zip.ZipOutputStream;

/**
 * 可复现的 ZIP 写出：条目时间固定为常量，同样的内容得到逐字节相同的包（崩溃恢复后的文件与一次跑完的相同）。
 * 关闭时只结束 ZIP，不关闭底层流。
 */
final class ExportZip implements AutoCloseable {

    /** ZIP 的 DOS 时间能表示的最早时刻；只写 DOS 时间，不加扩展时间戳。 */
    private static final LocalDateTime FIXED_TIME = LocalDateTime.of(1980, 1, 1, 0, 0);

    private final ZipOutputStream zip;

    ExportZip(OutputStream out) {
        this.zip = new ZipOutputStream(new FilterOutputStream(out) {
            @Override
            public void write(byte[] b, int off, int len) throws IOException {
                out.write(b, off, len);
            }

            @Override
            public void close() throws IOException {
                flush();
            }
        });
    }

    /** 开一个条目；写完一个条目前不要开下一个。返回的流写入的就是条目内容。 */
    OutputStream entry(String name) throws IOException {
        ZipEntry entry = new ZipEntry(name);
        entry.setTimeLocal(FIXED_TIME);
        zip.putNextEntry(entry);
        return new FilterOutputStream(zip) {
            @Override
            public void write(byte[] b, int off, int len) throws IOException {
                zip.write(b, off, len);
            }

            @Override
            public void close() throws IOException {
                zip.closeEntry();
            }
        };
    }

    @Override
    public void close() throws IOException {
        zip.finish();
        zip.close();
    }
}
