package cn.mjy.platform.delivery;

/** 503：触达能力没配好（主密钥缺失、该渠道没有渠道商、平台公开地址未配置）。失败即关闭，不降级发送。 */
public class DeliveryUnavailableException extends RuntimeException {

    public DeliveryUnavailableException(String message) {
        super(message);
    }
}
