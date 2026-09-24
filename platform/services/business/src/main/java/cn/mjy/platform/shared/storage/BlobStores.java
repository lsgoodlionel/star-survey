package cn.mjy.platform.shared.storage;

import java.nio.file.Path;

/**
 * 按 {@code platform.storage} 选一个 {@link BlobStore} 实现。
 *
 * <p><b>这是"换存储"的唯一开关</b>：导出与资产都从这里拿实现，所以不会出现
 * "导出切到对象存储了、资产还在本地卷上"。两处的"多副本需要共享卷"本来就是同一条遗留
 * （ADR 0015 决定 2 残余风险、ADR 0019 决定 2），修也只修一次。
 */
public final class BlobStores {

    private BlobStores() {
    }

    /**
     * @param localRoot 本地实现的根目录；{@code kind=s3} 时不用
     * @param keyPrefix 对象存储里这个模块的键前缀（同一个桶里各占一段）；本地实现不用——
     *                  本地是两个不同的根目录，本来就分开了
     */
    public static BlobStore create(BlobStoreProperties properties, Path localRoot, String keyPrefix) {
        return properties.isObjectStorage()
                ? new S3BlobStore(properties, keyPrefix)
                : new LocalBlobStore(localRoot);
    }
}
