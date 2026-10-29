package cn.mjy.platform.delivery;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import java.time.Duration;
import java.util.concurrent.atomic.AtomicReference;
import org.junit.jupiter.api.Test;

/** 不连公网 SMTP：覆盖供应商配置档、脱敏稳定 Message-ID 与失败分类。 */
class SmtpEmailDeliveryProviderTest {

    private static final Duration LEASE = Duration.ofMinutes(5);

    @Test
    void gmailUsesAnExplicitProfileAndPassesNoRawIdempotencyKeyIntoMessageId() {
        AtomicReference<String> observedId = new AtomicReference<>();
        DeliveryEmailProperties.Resolved configuration = properties("gmail", "app-password", "sender@gmail.com", "app-code")
                .resolve(LEASE);
        SmtpEmailDeliveryProvider provider = new SmtpEmailDeliveryProvider(configuration,
                (configured, message, messageId) -> {
                    assertThat(configured.profile()).isEqualTo(DeliveryEmailProperties.Profile.GMAIL);
                    assertThat(configured.profile().host()).isEqualTo("smtp.gmail.com");
                    assertThat(configured.profile().port()).isEqualTo(587);
                    assertThat(configured.profile().tlsMode())
                            .isEqualTo(DeliveryEmailProperties.TlsMode.STARTTLS);
                    observedId.set(messageId);
                    return messageId;
                });

        SendResult result = provider.send(message("task:recipient"));

        assertThat(result.status()).isEqualTo(SendResult.Status.ACCEPTED);
        assertThat(result.providerMessageId()).isEqualTo(observedId.get());
        assertThat(observedId.get()).startsWith("<mjy-").endsWith("@gmail.com>")
                .doesNotContain("task:recipient");
        assertThat(SmtpEmailDeliveryProvider.messageId("task:recipient", "gmail.com"))
                .isEqualTo(observedId.get());
    }

    @Test
    void outlookAndNeteaseProfilesKeepTheirTlsAndAuthenticationRequirementsDistinct() {
        DeliveryEmailProperties.Resolved outlook = properties("outlook-com", "oauth2", "sender@outlook.com", "oauth-token")
                .resolve(LEASE);
        DeliveryEmailProperties.Resolved netease = properties("netease-163", "app-password", "sender@163.com", "auth-code")
                .resolve(LEASE);

        assertThat(outlook.profile().host()).isEqualTo("smtp-mail.outlook.com");
        assertThat(outlook.profile().tlsMode()).isEqualTo(DeliveryEmailProperties.TlsMode.STARTTLS);
        assertThat(outlook.authMode()).isEqualTo(DeliveryEmailProperties.AuthMode.OAUTH2);
        assertThat(netease.profile().host()).isEqualTo("smtp.163.com");
        assertThat(netease.profile().port()).isEqualTo(465);
        assertThat(netease.profile().tlsMode()).isEqualTo(DeliveryEmailProperties.TlsMode.IMPLICIT_TLS);
        assertThat(netease.authMode()).isEqualTo(DeliveryEmailProperties.AuthMode.APP_PASSWORD);
    }

    @Test
    void mapsOnlySafeErrorCodesAndDoesNotExposeAddressOrCredential() {
        DeliveryEmailProperties.Resolved configuration = properties("gmail", "app-password", "sender@gmail.com", "private-code")
                .resolve(LEASE);
        SmtpEmailDeliveryProvider temporary = new SmtpEmailDeliveryProvider(configuration,
                (ignored, message, id) -> { throw new SmtpTransportException(SmtpTransportException.Kind.TEMPORARY); });
        SmtpEmailDeliveryProvider rejected = new SmtpEmailDeliveryProvider(configuration,
                (ignored, message, id) -> { throw new SmtpTransportException(SmtpTransportException.Kind.RECIPIENT_REJECTED); });

        SendResult retry = temporary.send(message("safe-key"));
        SendResult failure = rejected.send(message("safe-key"));

        assertThat(retry).isEqualTo(SendResult.retry("smtp_temporary_failure"));
        assertThat(failure).isEqualTo(SendResult.rejected("smtp_recipient_rejected"));
        assertThat(retry.errorCode()).doesNotContain("recipient@example.com", "private-code");
    }

    @Test
    void refusesIncompleteOrUnsafeEmailConfigurationBeforeRegisteringAProvider() {
        assertThatThrownBy(() -> properties("outlook-com", "oauth2", "sender@outlook.com", "").resolve(LEASE))
                .hasMessageContaining("credential");
        assertThatThrownBy(() -> properties("unsupported", "app-password", "sender@gmail.com", "code").resolve(LEASE))
                .hasMessageContaining("provider");
        assertThatThrownBy(() -> properties("gmail", "app-password", "sender@gmail.com", "code").resolve(Duration.ofSeconds(50)))
                .hasMessageContaining("send-lease");
        assertThatThrownBy(() -> properties("outlook-com", "app-password", "sender@outlook.com", "code")
                .resolve(LEASE)).hasMessageContaining("auth-mode");
    }

    private static DeliveryEmailProperties properties(String profile, String authMode, String address, String credential) {
        return new DeliveryEmailProperties(true, profile, authMode, address, address, credential,
                Duration.ofSeconds(10), Duration.ofSeconds(20), Duration.ofSeconds(20));
    }

    private static DeliveryMessage message(String key) {
        return new DeliveryMessage(DeliveryChannel.EMAIL, "recipient@example.com", "问卷邀请", "正文", key);
    }
}
