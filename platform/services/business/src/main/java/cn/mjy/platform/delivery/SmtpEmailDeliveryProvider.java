package cn.mjy.platform.delivery;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;

/**
 * 固定供应商配置档的 SMTP Email adapter。
 *
 * <p>SMTP 没有服务端幂等键协议。稳定的 Message-ID 只用于关联和人工排障，不能保证 DATA 后断线、
 * 租约恢复时不会重复投递；因此本 adapter 的语义是至少一次尝试，未知结果会被安全地标为可重试。
 * 已接受仅表示 SMTP 服务端接受了邮件，不能称为收件箱送达。
 */
final class SmtpEmailDeliveryProvider implements DeliveryProvider {

    private final DeliveryEmailProperties.Resolved configuration;
    private final SmtpTransport transport;

    SmtpEmailDeliveryProvider(DeliveryEmailProperties.Resolved configuration, SmtpTransport transport) {
        this.configuration = configuration;
        this.transport = transport;
    }

    @Override
    public DeliveryChannel channel() {
        return DeliveryChannel.EMAIL;
    }

    @Override
    public String name() {
        return "smtp-" + configuration.profile().code();
    }

    @Override
    public SendResult send(DeliveryMessage message) {
        if (message.channel() != DeliveryChannel.EMAIL) {
            return SendResult.rejected("smtp_channel_mismatch");
        }
        String messageId = messageId(message.idempotencyKey(), configuration.messageIdDomain());
        try {
            String acceptedId = transport.submit(configuration, message, messageId);
            return SendResult.accepted(acceptedId == null || acceptedId.isBlank() ? messageId : acceptedId);
        } catch (SmtpTransportException exception) {
            return switch (exception.kind()) {
                case RECIPIENT_REJECTED, AUTHENTICATION, CONFIGURATION -> SendResult.rejected(exception.kind().code());
                case TEMPORARY, UNKNOWN -> SendResult.retry(exception.kind().code());
            };
        }
    }

    static String messageId(String idempotencyKey, String domain) {
        try {
            byte[] digest = MessageDigest.getInstance("SHA-256")
                    .digest(idempotencyKey.getBytes(StandardCharsets.UTF_8));
            return "<mjy-" + java.util.HexFormat.of().formatHex(digest) + "@" + domain + ">";
        } catch (NoSuchAlgorithmException impossible) {
            throw new IllegalStateException("SHA-256 is required by the JRE", impossible);
        }
    }
}
