package cn.mjy.platform.response;

import org.springframework.boot.context.properties.EnableConfigurationProperties;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;

/** 注册导出配置与文件存储；无论定时调度是否启用都生效。 */
@Configuration(proxyBeanMethods = false)
@EnableConfigurationProperties(ResponseExportProperties.class)
class ResponseExportConfig {

    @Bean
    ExportFileStore exportFileStore(ResponseExportProperties properties) {
        return new LocalExportFileStore(properties.storageRoot());
    }
}
