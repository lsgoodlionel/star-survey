package cn.mjy.platform.shared.storage;

import java.time.Duration;
import java.util.Locale;
import org.springframework.boot.context.properties.ConfigurationProperties;
import org.springframework.boot.context.properties.bind.DefaultValue;

/**
 * 平台字节存储的**唯一**一份配置（前缀 {@code platform.storage}）。
 *
 * <p>导出文件与资产字节共用它：两处的"多副本部署需要共享卷"是同一条遗留
 * （ADR 0015 决定 2 残余风险、ADR 0019 决定 2），所以切到对象存储也只切一次。
 * 两个模块在**同一个桶**里各占一个键前缀（{@code exports/} 与 {@code assets/}），
 * 不各配一套端点、密钥与桶——那样迟早会出现"导出切过去了、资产还在本地卷上"。
 *
 * <p>{@code kind=local} 时其余字段全部不用，各模块仍按自己的目录配置落盘
 * （{@code platform.response.export.storage-dir} / {@code platform.asset.storage-dir}）。
 *
 * @param kind            {@code local}（缺省，开发与测试）或 {@code s3}（任何 S3 兼容对象存储）
 * @param endpoint        对象存储地址，如 {@code https://oss.example.com}；{@code kind=s3} 时必填
 * @param region          签名用的区域名，S3 兼容实现通常随便填一个固定值
 * @param bucket          桶名；{@code kind=s3} 时必填
 * @param accessKeyId     访问密钥 id；**从环境变量注入**，不写进配置文件
 * @param secretAccessKey 访问密钥；同上
 * @param pathStyle       true ＝ {@code <endpoint>/<bucket>/<key>}（MinIO、多数私有化部署）；
 *                        false ＝ 虚拟主机风格 {@code <bucket>.<endpoint>/<key>}
 * @param timeout         单次请求的超时
 */
@ConfigurationProperties("platform.storage")
public record BlobStoreProperties(
        @DefaultValue("local") String kind,
        @DefaultValue("") String endpoint,
        @DefaultValue("us-east-1") String region,
        @DefaultValue("") String bucket,
        @DefaultValue("") String accessKeyId,
        @DefaultValue("") String secretAccessKey,
        @DefaultValue("true") boolean pathStyle,
        @DefaultValue("PT60S") Duration timeout) {

    public static final String LOCAL = "local";
    public static final String S3 = "s3";

    public BlobStoreProperties {
        kind = kind == null ? LOCAL : kind.trim().toLowerCase(Locale.ROOT);
        endpoint = trimTrailingSlash(endpoint);
        if (!LOCAL.equals(kind) && !S3.equals(kind)) {
            throw new IllegalArgumentException("platform.storage.kind must be '" + LOCAL + "' or '" + S3 + "'");
        }
        if (timeout == null || timeout.isZero() || timeout.isNegative()) {
            throw new IllegalArgumentException("platform.storage.timeout must be positive");
        }
        if (S3.equals(kind)) {
            // 失败即关闭：配了一半的对象存储不能悄悄退回本地卷——那正是"多副本各写各的"
            // 这条故障的样子，而且要等到用户下载不到文件才发现。
            requireSet(endpoint, "endpoint");
            requireSet(bucket, "bucket");
            requireSet(region, "region");
            requireSet(accessKeyId, "access-key-id");
            requireSet(secretAccessKey, "secret-access-key");
        }
    }

    public boolean isObjectStorage() {
        return S3.equals(kind);
    }

    private static String trimTrailingSlash(String value) {
        return value == null ? "" : value.replaceAll("/+$", "");
    }

    private static void requireSet(String value, String name) {
        if (value == null || value.isBlank()) {
            throw new IllegalArgumentException("platform.storage." + name + " is required when kind=" + S3);
        }
    }
}
