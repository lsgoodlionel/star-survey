package cn.mjy.platform.survey.gateway;

/**
 * 发布网关客户端（契约 platform/contracts/publish-gateway-v1.md）。实现不得用异常表示网关结局，
 * 一切结局都归入 {@link GatewayOutcome}；拿不准的一律 {@link GatewayOutcome.Unknown}。
 */
public interface PublishGatewayClient {

    /** 地址与共享密钥是否已配置；未配置时平台拒绝发起发布（失败即关闭）。 */
    boolean isConfigured();

    GatewayOutcome publish(GatewayRequest request);
}
