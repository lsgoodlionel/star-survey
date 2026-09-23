package cn.mjy.platform.delivery;

import java.time.Duration;
import org.springframework.boot.context.properties.ConfigurationProperties;
import org.springframework.boot.context.properties.bind.DefaultValue;

/**
 * 触达配置（前缀 {@code platform.delivery}）。
 *
 * @param enabled        是否启用定时发送；<b>默认关闭</b>——没有真实渠道商凭据时不该有后台线程在跑，
 *                       任务 bean 始终可手动调用（测试即如此）
 * @param interval       两轮之间的间隔
 * @param initialDelay   启动后第一轮的延迟
 * @param batchSize      每轮最多处理的收件人数（跨所有租户合计）
 * @param ratePerWindow  每个租户每个限速窗口最多发送多少条
 * @param rateWindow     限速窗口长度
 * @param maxAttempts    同一个收件人最多尝试几次；达到后置为失败
 * @param sendLease      认领之后多久没有结果才当作"那个副本死了"并重新认领；必须明显长于渠道商调用的超时
 * @param backoff        任务暂时失败后的重试等待
 * @param linkTtl        链接签名参数的默认有效期
 * @param publicBaseUrl  平台对外 HTTPS 地址（短链、退订、回执回调都挂在它下面）；为空时短链与退订一律 503
 */
@ConfigurationProperties("platform.delivery")
public record DeliveryProperties(
        @DefaultValue("false") boolean enabled,
        @DefaultValue("PT10S") Duration interval,
        @DefaultValue("PT30S") Duration initialDelay,
        @DefaultValue("200") int batchSize,
        @DefaultValue("100") int ratePerWindow,
        @DefaultValue("PT1M") Duration rateWindow,
        @DefaultValue("3") int maxAttempts,
        @DefaultValue("PT5M") Duration sendLease,
        @DefaultValue("PT1M") Duration backoff,
        @DefaultValue("P30D") Duration linkTtl,
        @DefaultValue("") String publicBaseUrl) {

    public DeliveryProperties {
        requirePositive(interval, "interval");
        requirePositive(rateWindow, "rate-window");
        requirePositive(linkTtl, "link-ttl");
        if (initialDelay == null || initialDelay.isNegative()) {
            throw new IllegalArgumentException("platform.delivery.initial-delay must not be negative");
        }
        if (sendLease == null || sendLease.isNegative()) {
            throw new IllegalArgumentException("platform.delivery.send-lease must not be negative");
        }
        if (backoff == null || backoff.isNegative()) {
            throw new IllegalArgumentException("platform.delivery.backoff must not be negative");
        }
        if (batchSize < 1) {
            throw new IllegalArgumentException("platform.delivery.batch-size must be positive");
        }
        if (ratePerWindow < 1) {
            throw new IllegalArgumentException("platform.delivery.rate-per-window must be positive");
        }
        if (maxAttempts < 1) {
            throw new IllegalArgumentException("platform.delivery.max-attempts must be positive");
        }
        publicBaseUrl = publicBaseUrl == null ? "" : publicBaseUrl.replaceAll("/+$", "");
    }

    /** 平台对外地址；未配置时拒绝（失败即关闭，不生成指向 localhost 的链接）。 */
    public String requirePublicBaseUrl() {
        if (publicBaseUrl.isBlank()) {
            throw new DeliveryUnavailableException("platform.delivery.public-base-url is not configured");
        }
        return publicBaseUrl;
    }

    private static void requirePositive(Duration value, String name) {
        if (value == null || value.isZero() || value.isNegative()) {
            throw new IllegalArgumentException("platform.delivery." + name + " must be positive");
        }
    }
}
