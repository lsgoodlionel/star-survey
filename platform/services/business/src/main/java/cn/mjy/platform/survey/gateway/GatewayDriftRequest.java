package cn.mjy.platform.survey.gateway;

import java.util.Objects;

/**
 * 一次漂移检查（契约 publish-gateway-v1.2 的 {@code POST /v1/drift-check}）。
 * bindingJson 是平台存档的绑定记录原文，可为空；带上时网关能指名道姓地报出被改的题目。
 */
public record GatewayDriftRequest(String engineInstanceId, int surveyId, String expectedFingerprint,
        String bindingJson) {

    public GatewayDriftRequest {
        Objects.requireNonNull(engineInstanceId, "engineInstanceId");
        Objects.requireNonNull(expectedFingerprint, "expectedFingerprint");
        if (surveyId <= 0) {
            throw new IllegalArgumentException("surveyId must be positive");
        }
    }
}
