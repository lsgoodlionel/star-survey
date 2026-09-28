package cn.mjy.platform.asset;

import java.time.Instant;
import java.util.UUID;

/**
 * 一件作答者上传的资产在答卷里的位置。
 *
 * <p><b>不含作答者指纹</b>——指纹是判定用的，不是展示用的。这里只给「有没有绑定」一个布尔，
 * 免得它顺着 REST 应答流出去（指纹虽不可逆，但泄漏它等于泄漏「这两份上传是同一个人的」）。
 */
public record ResponderAssetView(UUID assetId, String questionCode, AssetKind kind, String contentType,
        long byteSize, boolean hasRespondentBinding, Instant ingestedAt) {
}
