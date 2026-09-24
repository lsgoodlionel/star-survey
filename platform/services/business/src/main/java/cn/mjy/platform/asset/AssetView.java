package cn.mjy.platform.asset;

import java.time.Instant;
import java.util.UUID;

/** 资产的对外视图：当前版本的元数据随行给出，省掉一次往返。**不含存储键**。 */
public record AssetView(UUID id, String name, AssetKind kind, AssetStatus status, int currentVersion,
        String contentType, long byteSize, String sha256, Instant createdAt, Instant updatedAt) {
}
