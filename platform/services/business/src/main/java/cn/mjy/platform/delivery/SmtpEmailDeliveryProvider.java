package cn.mjy.platform.delivery;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.util.function.Function;
import java.util.function.Predicate;

/**
 * 固定供应商配置档的 SMTP Email adapter。
 *
 * <p>SMTP 没有服务端幂等键协议。稳定的 Message-ID 只用于关联和人工排障，不能保证 DATA 后断线、
 * 租约恢复时不会重复投递；因此本 adapter 的语义是至少一次尝试，未知结果会被安全地标为可重试。
 * 已接受仅表示 SMTP 服务端接受了邮件，不能称为收件箱送达。
 */
final class SmtpEmailDeliveryProvider implements DeliveryProvider {

    private final DeliveryEmailProperties.Resolved configuration;
    private final Function<DeliveryMessage, DeliveryEmailProperties.Resolved> resolver;
    private final SmtpTransport transport;
    private final Predicate<cn.mjy.platform.shared.TenantId> availability;

    SmtpEmailDeliveryProvider(DeliveryEmailProperties.Resolved configuration, SmtpTransport transport) {
        this.configuration = configuration;
        this.resolver = ignored -> configuration;
        this.transport = transport;
        this.availability = ignored -> true;
    }

    SmtpEmailDeliveryProvider(Function<DeliveryMessage, DeliveryEmailProperties.Resolved> resolver,
            Predicate<cn.mjy.platform.shared.TenantId> availability, SmtpTransport transport) {
        this.configuration = null;
        this.resolver = resolver;
        this.availability = availability;
        this.transport = transport;
    }

    @Override
    public boolean available(cn.mjy.platform.shared.TenantId tenant) {
        return availability.test(tenant);
    }

    @Override
    public DeliveryChannel channel() {
        return DeliveryChannel.EMAIL;
    }

    @Override
    public String name() {
        return configuration == null ? "smtp-configured" : "smtp-" + configuration.profile().code();
    }

    @Override
    public SendResult send(DeliveryMessage message) {
        if (message.channel() != DeliveryChannel.EMAIL) {
            return SendResult.rejected("smtp_channel_mismatch");
        }
        DeliveryEmailProperties.Resolved selected = resolver.apply(message);
        if (selected == null) return SendResult.rejected("email_not_configured");
        String messageId = messageId(message.idempotencyKey(), selected.messageIdDomain());
        try {
            String acceptedId = transport.submit(selected, message, messageId);
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
