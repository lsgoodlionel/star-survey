package cn.mjy.platform.delivery;

import org.springframework.boot.context.properties.EnableConfigurationProperties;
import org.springframework.context.annotation.Configuration;

/** 注册触达配置；无论定时调度是否启用都生效（任务本身始终可手动调用）。 */
@Configuration(proxyBeanMethods = false)
@EnableConfigurationProperties(DeliveryProperties.class)
class DeliveryConfig {
}
