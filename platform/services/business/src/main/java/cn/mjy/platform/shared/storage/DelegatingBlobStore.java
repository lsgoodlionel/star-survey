package cn.mjy.platform.shared.storage;

import java.io.IOException;
import java.io.InputStream;

/**
 * 只转发的壳。各模块的存储类型（{@code ExportFileStore} / {@code AssetFileStore}）是给 Spring
 * 按类型注入用的名字，本身不加任何语义；壳里装的才是按 {@code platform.storage} 选出来的实现。
 *
 * <p>有了它，"换成对象存储"只改 {@link BlobStores#create} 一处，两个模块同时切过去——
 * 而不是各自 new 一个本地实现，然后某一天只有一处记得改。
 */
public abstract class DelegatingBlobStore implements BlobStore {

    private final BlobStore delegate;

    protected DelegatingBlobStore(BlobStore delegate) {
        this.delegate = delegate;
    }

    /** 壳里装的那一个。诊断与接线测试用。 */
    public BlobStore delegate() {
        return delegate;
    }

    @Override
    public Upload create(String key) throws IOException {
        return delegate.create(key);
    }

    @Override
    public InputStream open(String key) throws IOException {
        return delegate.open(key);
    }

    @Override
    public boolean exists(String key) {
        return delegate.exists(key);
    }

    @Override
    public long size(String key) throws IOException {
        return delegate.size(key);
    }

    @Override
    public void delete(String key) throws IOException {
        delegate.delete(key);
    }

    @Override
    public void deletePrefix(String prefix) throws IOException {
        delegate.deletePrefix(prefix);
    }
}
