package cn.mjy.platform.survey.gateway;

import java.util.Objects;
import java.util.UUID;

/**
 * 收口一份被取代的引擎问卷（契约 publish-gateway-v1.2 的 {@code POST /v1/close}）。
 * requestId 只用于日志关联：收口天然幂等，失败后换新的 requestId 重试即可。
 */
public record GatewayCloseRequest(UUID requestId, String engineInstanceId, int surveyId) {

    public GatewayCloseRequest {
        Objects.requireNonNull(requestId, "requestId");
        Objects.requireNonNull(engineInstanceId, "engineInstanceId");
        if (surveyId <= 0) {
            throw new IllegalArgumentException("surveyId must be positive");
        }
    }
}
