package cn.mjy.platform.response;

import cn.mjy.platform.shared.storage.BlobStore;
import cn.mjy.platform.shared.storage.BlobStoreProperties;
import cn.mjy.platform.shared.storage.BlobStores;
import cn.mjy.platform.shared.storage.DelegatingBlobStore;
import org.springframework.boot.context.properties.EnableConfigurationProperties;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;

/** 注册导出配置与文件存储；无论定时调度是否启用都生效。 */
@Configuration(proxyBeanMethods = false)
@EnableConfigurationProperties(ResponseExportProperties.class)
class ResponseExportConfig {

    /** 对象存储里导出文件占的那一段键前缀（资产占 {@code assets/}）。 */
    static final String KEY_PREFIX = "exports";

    /**
     * 落在哪里由共用的 {@code platform.storage} 决定（本地目录还是对象存储），
     * 与资产服务**同一个开关**——两处的"多副本需共享卷"是同一条遗留，不各修一套。
     */
    @Bean
    ExportFileStore exportFileStore(BlobStoreProperties storage, ResponseExportProperties properties) {
        return new ExportBlobStore(BlobStores.create(storage, properties.storageRoot(), KEY_PREFIX));
    }

    /** 只是给注入用的类型名；机制全在壳里装的那一个实现上。 */
    private static final class ExportBlobStore extends DelegatingBlobStore implements ExportFileStore {

        private ExportBlobStore(BlobStore delegate) {
            super(delegate);
        }
    }
}
