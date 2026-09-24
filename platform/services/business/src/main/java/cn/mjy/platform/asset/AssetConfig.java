package cn.mjy.platform.asset;

import cn.mjy.platform.shared.storage.LocalBlobStore;
import java.nio.file.Path;
import org.springframework.boot.context.properties.EnableConfigurationProperties;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;

/** 注册资产配置与字节存储。 */
@Configuration(proxyBeanMethods = false)
@EnableConfigurationProperties(AssetProperties.class)
class AssetConfig {

    @Bean
    AssetFileStore assetFileStore(AssetProperties properties) {
        return new LocalAssetFileStore(properties.storageRoot());
    }

    /** 机制全在 {@link LocalBlobStore}，这里只把它接到资产模块的类型上。 */
    private static final class LocalAssetFileStore extends LocalBlobStore implements AssetFileStore {

        private LocalAssetFileStore(Path root) {
            super(root);
        }
    }
}
