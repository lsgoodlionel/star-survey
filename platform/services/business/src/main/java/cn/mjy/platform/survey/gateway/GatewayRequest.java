package cn.mjy.platform.survey.gateway;

import java.util.Objects;
import java.util.UUID;

/**
 * 一次发布请求（契约 publish-gateway-v1 的 {@code POST /v1/publish} 请求体）。
 * definitionJson 是已校验、已写入问卷公开 UUID 的定义原文；同一 requestId 的重试必须发同样的内容。
 */
public record GatewayRequest(UUID requestId, String engineInstanceId, String definitionJson) {

    public GatewayRequest {
        Objects.requireNonNull(requestId, "requestId");
        Objects.requireNonNull(engineInstanceId, "engineInstanceId");
        Objects.requireNonNull(definitionJson, "definitionJson");
    }
}
