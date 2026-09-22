package cn.mjy.platform.survey;

import org.springframework.boot.context.properties.EnableConfigurationProperties;
import org.springframework.context.annotation.Configuration;

/** 注册漂移巡检配置；无论定时巡检是否启用都生效（按需检查与手动巡检始终可用）。 */
@Configuration(proxyBeanMethods = false)
@EnableConfigurationProperties(DriftMonitorProperties.class)
class DriftMonitorConfig {
}
