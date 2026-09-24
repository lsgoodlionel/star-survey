package cn.mjy.platform.shared.storage;

import java.io.Closeable;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.util.regex.Pattern;

/**
 * 平台的对象存储抽象：**导出文件与资产共用这一份**（ADR 0015 决定 2 立的形状，ADR 0019 决定 2 提到这里）。
 * 于是"生产换成对象存储"只需要做一次实现，两边同时切过去，不会出现两套各自为政的存储。
 *
 * <p>键是 {@code /} 分隔的小写段，只含 {@code [a-z0-9.-]}，一律由平台生成
 * （租户 / 作业 / 分片名，或 租户 / 资产 / 版本），<b>从不含调用方文本</b>——
 * 原始文件名只作为元数据存在库里，不参与路径。
 *
 * <p>写入原子：{@link Upload#commit()} 之后对象才出现（或整体替换旧对象），读者看不到半个文件；
 * 没有提交就关闭（写入出错、执行者死掉）时旧对象保持不变。
 */
public interface BlobStore {

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
            throw new IllegalArgumentException("invalid blob key");
        }
        return key;
    }
}
