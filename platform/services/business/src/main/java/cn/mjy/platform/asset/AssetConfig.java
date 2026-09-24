package cn.mjy.platform.asset;

import cn.mjy.platform.response.RespondentSource;
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

    /**
     * 「这份答卷是用哪个邀请码答的」的适配器（ADR 0016 缺口 b 的既有读端点）。
     *
     * <p><b>读不到就往上抛，不吞成空表。</b>吞掉的后果是悄悄入库一批没有身份绑定的资产，
     * 而那些资产此后<b>永远签不出取件票</b>——一次网关抖动会变成一批作答者再也看不到自己的上传。
     * 匿名问卷本来就返回空表（不抛），两种情况因此分得开。
     */
    @Bean
    ResponderUploadPuller.RespondentTokens respondentTokens(RespondentSource respondents) {
        return respondents::readRespondents;
    }

    /** 机制全在 {@link LocalBlobStore}，这里只把它接到资产模块的类型上。 */
    private static final class LocalAssetFileStore extends LocalBlobStore implements AssetFileStore {

        private LocalAssetFileStore(Path root) {
            super(root);
        }
    }
}
