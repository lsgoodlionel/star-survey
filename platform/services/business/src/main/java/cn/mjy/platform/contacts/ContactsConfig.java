package cn.mjy.platform.contacts;

import org.springframework.boot.context.properties.EnableConfigurationProperties;
import org.springframework.context.annotation.Configuration;

/** 通讯录模块的配置绑定；定时器另见 {@link ContactImportScheduler}，可单独关闭。 */
@Configuration(proxyBeanMethods = false)
@EnableConfigurationProperties(ContactImportProperties.class)
class ContactsConfig {
}
