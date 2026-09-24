package cn.mjy.platform.asset;

import cn.mjy.platform.shared.storage.BlobStore;
import cn.mjy.platform.shared.storage.BlobStoreProperties;
import cn.mjy.platform.shared.storage.BlobStores;
import cn.mjy.platform.shared.storage.DelegatingBlobStore;
import org.springframework.boot.context.properties.EnableConfigurationProperties;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;

/** 注册资产配置与字节存储。 */
@Configuration(proxyBeanMethods = false)
@EnableConfigurationProperties(AssetProperties.class)
class AssetConfig {

    /** 对象存储里资产字节占的那一段键前缀（导出占 {@code exports/}）。 */
    static final String KEY_PREFIX = "assets";

    /**
     * 落在哪里由共用的 {@code platform.storage} 决定（本地目录还是对象存储），
     * 与导出文件**同一个开关**——两处的"多副本需共享卷"是同一条遗留，不各修一套。
     */
    @Bean
    AssetFileStore assetFileStore(BlobStoreProperties storage, AssetProperties properties) {
        return new AssetBlobStore(BlobStores.create(storage, properties.storageRoot(), KEY_PREFIX));
    }

    /** 只是给注入用的类型名；机制全在壳里装的那一个实现上。 */
    private static final class AssetBlobStore extends DelegatingBlobStore implements AssetFileStore {

        private AssetBlobStore(BlobStore delegate) {
            super(delegate);
        }
    }
}
