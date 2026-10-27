package cn.mjy.platform.survey.gateway;

import java.util.Objects;
import java.util.List;
import java.util.UUID;

/**
 * 一次发布请求（契约 publish-gateway-v1 的 {@code POST /v1/publish} 请求体）。
 * definitionJson 是已校验、已写入问卷公开 UUID 的定义原文；同一 requestId 的重试必须发同样的内容。
 */
public record GatewayRequest(UUID requestId, String engineInstanceId, String definitionJson,
        List<BrandAsset> brandAssets) {

    /** A bounded, verified brand file.  The byte payload is never logged. */
    public record BrandAsset(String slot, String fileName, String contentType, long sizeBytes,
            String sha256, String contentBase64) {
        public BrandAsset {
            Objects.requireNonNull(slot, "slot");
            Objects.requireNonNull(fileName, "fileName");
            Objects.requireNonNull(contentType, "contentType");
            Objects.requireNonNull(sha256, "sha256");
            Objects.requireNonNull(contentBase64, "contentBase64");
        }
    }

    public GatewayRequest(UUID requestId, String engineInstanceId, String definitionJson) {
        this(requestId, engineInstanceId, definitionJson, List.of());
    }

    public GatewayRequest {
        Objects.requireNonNull(requestId, "requestId");
        Objects.requireNonNull(engineInstanceId, "engineInstanceId");
        Objects.requireNonNull(definitionJson, "definitionJson");
        brandAssets = List.copyOf(Objects.requireNonNull(brandAssets, "brandAssets"));
    }
}
