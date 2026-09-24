package cn.mjy.platform.asset;

import java.nio.file.Path;
import java.time.Duration;
import org.springframework.boot.context.properties.ConfigurationProperties;
import org.springframework.boot.context.properties.bind.DefaultValue;

/**
 * 资产服务配置（前缀 {@code platform.asset}，ADR 0019）。
 *
 * @param storageDir    字节落在哪（本地实现；多副本需共享卷，生产换对象存储实现）；为空取系统临时目录下的 mjy-assets
 * @param maxBytes      单个文件的字节上限，超过即 400
 * @param ticketTtl     发布时签发的取件票有效期；到期后作答页上的图会失效，需改版再发布
 * @param publicBaseUrl 平台对外 HTTPS 地址，取件地址拼在它下面；为空则一律不签发（失败即关闭）
 */
@ConfigurationProperties("platform.asset")
public record AssetProperties(
        @DefaultValue("") String storageDir,
        @DefaultValue("16777216") long maxBytes,
        @DefaultValue("P180D") Duration ticketTtl,
        @DefaultValue("") String publicBaseUrl) {

    private static final String DEFAULT_DIR_NAME = "mjy-assets";

    public Path storageRoot() {
        String dir = storageDir.isBlank() ? System.getProperty("java.io.tmpdir") + "/" + DEFAULT_DIR_NAME : storageDir;
        return Path.of(dir);
    }

    /** 末尾斜杠一律去掉，拼地址时只加一次。 */
    public String publicBaseUrl() {
        return publicBaseUrl.replaceAll("/+$", "");
    }
}
