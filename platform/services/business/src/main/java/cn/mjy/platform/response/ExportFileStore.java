package cn.mjy.platform.response;

import java.io.Closeable;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.util.regex.Pattern;

/**
 * 导出文件的对象存储抽象（ADR 0015 决定 2）。键是 {@code /} 分隔的小写段，只含 {@code [a-z0-9.-]}，
 * 由平台生成（租户 / 作业 / 分片名），从不含调用方文本。
 *
 * <p>写入原子：{@link Upload#commit()} 之后对象才出现（或整体替换旧对象），读者看不到半个文件；
 * 没有提交就关闭（写入出错、执行者死掉）时旧对象保持不变。
 */
public interface ExportFileStore {

    Pattern KEY = Pattern.compile("[a-z0-9][a-z0-9.-]{0,127}(/[a-z0-9][a-z0-9.-]{0,127}){0,7}");

    /** 一次写入：往 {@link #stream()} 写，{@link #commit()} 提交；未提交即 {@link #close()} 等于放弃。 */
    interface Upload extends Closeable {

        OutputStream stream();

        void commit() throws IOException;
    }

    /** 新建或整体替换一个对象。 */
    Upload create(String key) throws IOException;

    InputStream open(String key) throws IOException;

    boolean exists(String key);

    long size(String key) throws IOException;

    void delete(String key) throws IOException;

    /** 删除某前缀（一个作业的全部文件）下的所有对象；不存在不报错。 */
    void deletePrefix(String prefix) throws IOException;

    static String requireKey(String key) {
        if (key == null || !KEY.matcher(key).matches() || key.contains("..")) {
            throw new IllegalArgumentException("invalid export file key");
        }
        return key;
    }
}
