package cn.mjy.platform.asset;

import java.time.Instant;

/** 一个版本的元数据。带存储键，因此<b>只在服务内部流转</b>，不进任何 REST 应答。 */
public record AssetVersionView(int versionNo, String contentType, long byteSize, String sha256,
        String originalName, String storageKey, Instant createdAt) {
}
