package cn.mjy.platform.identity.org;

import java.time.Duration;
import org.springframework.boot.context.properties.ConfigurationProperties;
import org.springframework.boot.context.properties.bind.DefaultValue;

/**
 * 通讯录事件回调配置（前缀 {@code platform.identity.org-events}）。
 *
 * @param maxBodyBytes 请求体上限；超过即 413，不读完、不解析
 * @param maxClockSkew 请求时间戳与收到时间的最大偏差；超出按验签失败（401）处理
 */
@ConfigurationProperties("platform.identity.org-events")
public record OrgEventProperties(
        @DefaultValue("65536") int maxBodyBytes,
        @DefaultValue("PT5M") Duration maxClockSkew) {

    public OrgEventProperties {
        if (maxBodyBytes <= 0) {
            throw new IllegalArgumentException("platform.identity.org-events.max-body-bytes must be positive");
        }
        if (maxClockSkew == null || maxClockSkew.isNegative() || maxClockSkew.isZero()) {
            throw new IllegalArgumentException("platform.identity.org-events.max-clock-skew must be positive");
        }
    }
}
