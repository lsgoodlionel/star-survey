package cn.mjy.platform.survey.gateway;

import java.util.Objects;
import java.util.UUID;

/**
 * 撤销一个邀请码：删掉引擎里那一行参与者（契约 publish-gateway-v1.4 的
 * {@code POST /v1/participants/revoke}）。requestId 只用于日志关联——撤销天然幂等，
 * 失败后换新的 requestId 重试即可。
 *
 * <p>实例与 sid 取自平台自己那条映射（{@code contact_participation}），不是"当前在线版本"：
 * 令牌活在哪份引擎问卷上，就到那份上去删（ADR 0012 的新版本＝新引擎问卷，旧版本的码要在旧 sid 上撤）。
 *
 * <p>{@code participantToken} 是能直接进入问卷的凭据，因此 {@link #toString()} 不含它。
 */
public record GatewayRevokeRequest(UUID requestId, String engineInstanceId, int surveyId,
        String participantToken) {

    public GatewayRevokeRequest {
        Objects.requireNonNull(requestId, "requestId");
        Objects.requireNonNull(engineInstanceId, "engineInstanceId");
        Objects.requireNonNull(participantToken, "participantToken");
        if (surveyId <= 0) {
            throw new IllegalArgumentException("surveyId must be positive");
        }
        if (participantToken.isBlank()) {
            throw new IllegalArgumentException("participantToken must not be blank");
        }
    }

    @Override
    public String toString() {
        return "GatewayRevokeRequest[requestId=" + requestId + ", engineInstanceId=" + engineInstanceId
                + ", surveyId=" + surveyId + ", participantToken=***]";
    }
}
