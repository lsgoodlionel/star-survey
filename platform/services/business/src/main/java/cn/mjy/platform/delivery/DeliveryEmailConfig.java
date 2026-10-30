package cn.mjy.platform.delivery;

import org.springframework.boot.context.properties.EnableConfigurationProperties;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.boot.autoconfigure.condition.ConditionalOnMissingBean;
import org.springframework.context.annotation.Profile;

/** 注册租户级 SMTP adapter；没有租户配置或静态回退时 provider 会明确报告不可用。SMS 本轮保持未配置。 */
@Configuration(proxyBeanMethods = false)
@EnableConfigurationProperties(DeliveryEmailProperties.class)
class DeliveryEmailConfig {

    @Bean
    SmtpTransport smtpTransport() {
        return new JakartaMailSmtpTransport();
    }

    @Bean
    @ConditionalOnMissingBean(DeliveryProvider.class)
    @Profile("!test")
    DeliveryProvider smtpEmailDeliveryProvider(DeliveryEmailProperties email, DeliveryProperties delivery,
            EmailSettingsService settings, SmtpTransport transport) {
        DeliveryEmailProperties.Resolved fallback = email.enabled() ? email.resolve(delivery.sendLease()) : null;
        return new SmtpEmailDeliveryProvider(message -> {
            DeliveryEmailProperties.Resolved configured = settings.resolve(message.tenant());
            return configured == null ? fallback : configured;
        }, tenant -> settings.isConfigured(tenant) || fallback != null, transport);
    }
}
