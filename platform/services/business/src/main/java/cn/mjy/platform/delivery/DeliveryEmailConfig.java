package cn.mjy.platform.delivery;

import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.boot.context.properties.EnableConfigurationProperties;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;

/** 只在明确启用 Email 时注册 SMTP adapter；SMS 本轮保持未配置。 */
@Configuration(proxyBeanMethods = false)
@EnableConfigurationProperties(DeliveryEmailProperties.class)
class DeliveryEmailConfig {

    @Bean
    @ConditionalOnProperty(prefix = "platform.delivery.email", name = "enabled", havingValue = "true")
    DeliveryProvider smtpEmailDeliveryProvider(DeliveryEmailProperties email, DeliveryProperties delivery) {
        return new SmtpEmailDeliveryProvider(email.resolve(delivery.sendLease()), new JakartaMailSmtpTransport());
    }
}
