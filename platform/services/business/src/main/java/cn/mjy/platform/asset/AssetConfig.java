package cn.mjy.platform.asset;

import cn.mjy.platform.response.RespondentSource;
import cn.mjy.platform.response.ResponseAnswersUnavailableException;
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
     *
     * <p><b>但必须换成资产模块自己的异常</b>：答卷模块的 {@code ResponseAnswersUnavailableException}
     * 从资产控制器抛出去会落到没人接的地方，对外变成 500——「失败即关闭」照样成立
     * （确实没拉进来），可状态码骗人。这正是上一轮独立安全审查抓到的那一类
     * （ADR 0019 审查表里的 MEDIUM）。语义本来就是"这件事当下做不了"，503。
     */
    @Bean
    ResponderUploadPuller.RespondentTokens respondentTokens(RespondentSource respondents) {
        return (instanceId, sid, responseIds) -> {
            try {
                return respondents.readRespondents(instanceId, sid, responseIds);
            } catch (ResponseAnswersUnavailableException e) {
                throw new ResponderUploadsUnavailableException(
                        "cannot tell who the respondents are: " + e.getMessage());
            }
        };
    }

    /** 机制全在 {@link LocalBlobStore}，这里只把它接到资产模块的类型上。 */
    private static final class LocalAssetFileStore extends LocalBlobStore implements AssetFileStore {

        private LocalAssetFileStore(Path root) {
            super(root);
        }
    }
}
