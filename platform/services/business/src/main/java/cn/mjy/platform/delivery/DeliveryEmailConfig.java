package cn.mjy.platform.delivery;

import org.springframework.boot.context.properties.EnableConfigurationProperties;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;

/** 只在明确启用 Email 时注册 SMTP adapter；SMS 本轮保持未配置。 */
@Configuration(proxyBeanMethods = false)
@EnableConfigurationProperties(DeliveryEmailProperties.class)
class DeliveryEmailConfig {

    @Bean
    SmtpTransport smtpTransport() {
        return new JakartaMailSmtpTransport();
    }

    @Bean
    DeliveryProvider smtpEmailDeliveryProvider(DeliveryEmailProperties email, DeliveryProperties delivery,
            EmailSettingsService settings, SmtpTransport transport) {
        DeliveryEmailProperties.Resolved fallback = email.enabled() ? email.resolve(delivery.sendLease()) : null;
        return new SmtpEmailDeliveryProvider(message -> {
            DeliveryEmailProperties.Resolved configured = settings.resolve(message.tenant());
            return configured == null ? fallback : configured;
        }, transport);
    }
}
