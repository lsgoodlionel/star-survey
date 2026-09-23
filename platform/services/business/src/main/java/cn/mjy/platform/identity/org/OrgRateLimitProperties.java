package cn.mjy.platform.identity.org;

import org.springframework.boot.context.properties.ConfigurationProperties;
import org.springframework.boot.context.properties.bind.DefaultValue;

/**
 * 匿名端点的限流配置（前缀 {@code platform.identity.rate-limit}）：
 * 事件回调 {@code /v1/org-events/**} 与免登 {@code /v1/auth/org/**} 各自一套桶，互不相干。
 *
 * @param perConnectionPerMinute       每「来源地址 × 连接」每分钟的未验签请求数
 * @param perSourcePerMinute           每来源地址每分钟的未验签请求数（跨连接，挡住换连接号乱撒的扫描）
 * @param verifiedPerConnectionPerMinute 每连接每分钟的已验签请求数；未验签的噪声碰不到这份额度
 * @param maxBuckets                   每个桶集合的条目上限，超出按最近使用淘汰
 * @param retryAfterSeconds            429 的 {@code Retry-After}
 */
@ConfigurationProperties("platform.identity.rate-limit")
public record OrgRateLimitProperties(
        @DefaultValue("true") boolean enabled,
        @DefaultValue("60") int perConnectionPerMinute,
        @DefaultValue("300") int perSourcePerMinute,
        @DefaultValue("600") int verifiedPerConnectionPerMinute,
        @DefaultValue("20000") int maxBuckets,
        @DefaultValue("60") int retryAfterSeconds) {

    public OrgRateLimitProperties {
        positive("per-connection-per-minute", perConnectionPerMinute);
        positive("per-source-per-minute", perSourcePerMinute);
        positive("verified-per-connection-per-minute", verifiedPerConnectionPerMinute);
        positive("max-buckets", maxBuckets);
        positive("retry-after-seconds", retryAfterSeconds);
    }

    private static void positive(String name, int value) {
        if (value <= 0) {
            throw new IllegalArgumentException("platform.identity.rate-limit." + name + " must be positive");
        }
    }
}
