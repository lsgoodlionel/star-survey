package cn.mjy.platform.contacts;

import cn.mjy.platform.survey.gateway.GatewayRevokeRequest;
import cn.mjy.platform.survey.gateway.PublishGatewayClient;
import cn.mjy.platform.survey.gateway.RevokeOutcome;
import java.util.UUID;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Component;

/**
 * 把一条邀请码映射在**引擎侧**作废：经发布网关删掉 {@code tokens_<sid>} 里那一行参与者
 * （契约 publish-gateway-v1.4，ADR 0016 缺口「邀请码撤销的平台接口」）。
 *
 * <p>只做一件事，因此单独成类：{@link ContactParticipationService} 由此不必认识网关，
 * 而"引擎没确认就不许写 {@code revoked_at}"这条规矩也有了唯一的落点。
 *
 * <p>失败即关闭：网关没给出 {@link RevokeOutcome.Revoked} 就抛
 * {@link InvitationRevocationFailedException}，调用方据此整笔放弃。**宁可撤销失败，
 * 也不要平台记着"已撤销"而令牌还能打开问卷**——后者没有任何人会再发现。
 *
 * <p>令牌是凭据：日志里只有映射 id、实例与 sid，绝不出现令牌本身。
 */
@Component
class EngineInvitationRevocation {

    private static final Logger log = LoggerFactory.getLogger(EngineInvitationRevocation.class);

    private final PublishGatewayClient gateway;

    EngineInvitationRevocation(PublishGatewayClient gateway) {
        this.gateway = gateway;
    }

    /**
     * 让这条映射的令牌在引擎里立即失效。
     *
     * @throws InvitationRevocationFailedException 网关未确认（含网关未配置、超时、引擎失败）
     */
    void revoke(ParticipationView participation) {
        GatewayRevokeRequest request = new GatewayRevokeRequest(UUID.randomUUID(),
                participation.engineInstanceId(), participation.engineSid(), participation.participantToken());
        RevokeOutcome outcome = call(request, participation);
        switch (outcome) {
            case RevokeOutcome.Revoked revoked -> log.info(
                    "revoked participation {} in engine {} sid {}{}", participation.id(),
                    participation.engineInstanceId(), participation.engineSid(),
                    revoked.alreadyAbsent() ? " (the engine no longer had it)" : "");
            case RevokeOutcome.NotRevoked notRevoked -> {
                log.warn("participation {} was NOT revoked in engine {} sid {}: {}", participation.id(),
                        participation.engineInstanceId(), participation.engineSid(), notRevoked.reason());
                throw new InvitationRevocationFailedException("the invitation code of participation "
                        + participation.id() + " is still valid in the engine: " + notRevoked.reason());
            }
        }
    }

    private RevokeOutcome call(GatewayRevokeRequest request, ParticipationView participation) {
        try {
            return gateway.revokeParticipant(request);
        } catch (RuntimeException e) {
            // 客户端契约说它不抛异常；万一抛了也不能让它冒充成功。
            log.error("publish gateway client failed revoking participation {}", participation.id(), e);
            return new RevokeOutcome.NotRevoked("client error: " + e.getClass().getSimpleName());
        }
    }
}
