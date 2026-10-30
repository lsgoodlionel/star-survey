package cn.mjy.platform.delivery;

/** 窄 SMTP 端口：让投递语义可在无网络、无真实凭据时测试。 */
interface SmtpTransport {

    /** 只建立并认证 SMTP 会话，绝不构造或提交邮件。 */
    default void verify(DeliveryEmailProperties.Resolved configuration) throws SmtpTransportException {
        throw new SmtpTransportException(SmtpTransportException.Kind.CONFIGURATION);
    }

    String submit(DeliveryEmailProperties.Resolved configuration, DeliveryMessage message, String messageId)
            throws SmtpTransportException;
}
