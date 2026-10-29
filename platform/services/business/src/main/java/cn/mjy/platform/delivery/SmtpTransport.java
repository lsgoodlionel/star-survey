package cn.mjy.platform.delivery;

/** 窄 SMTP 端口：让投递语义可在无网络、无真实凭据时测试。 */
interface SmtpTransport {

    String submit(DeliveryEmailProperties.Resolved configuration, DeliveryMessage message, String messageId)
            throws SmtpTransportException;
}
