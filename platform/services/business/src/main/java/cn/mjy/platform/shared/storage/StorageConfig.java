package cn.mjy.platform.shared.storage;

import org.springframework.boot.context.properties.EnableConfigurationProperties;
import org.springframework.context.annotation.Configuration;

/** 注册平台字节存储的那一份共用配置（导出与资产都读它）。 */
@Configuration(proxyBeanMethods = false)
@EnableConfigurationProperties(BlobStoreProperties.class)
class StorageConfig {
}
