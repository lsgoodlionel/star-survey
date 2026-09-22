package cn.mjy.platform.survey;

import org.springframework.boot.context.properties.EnableConfigurationProperties;
import org.springframework.context.annotation.Configuration;

/** 注册核对配置；无论定时调度是否启用都生效（任务本身始终可手动调用）。 */
@Configuration(proxyBeanMethods = false)
@EnableConfigurationProperties(PublishReconciliationProperties.class)
class PublishReconciliationConfig {
}
