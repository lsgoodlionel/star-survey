package cn.mjy.platform.survey;

import java.time.Duration;
import org.springframework.boot.context.properties.ConfigurationProperties;
import org.springframework.boot.context.properties.bind.DefaultValue;

/**
 * 发布核对任务的配置（前缀 {@code platform.survey.reconcile}）。
 *
 * @param enabled        是否启用定时调度；关闭时任务 bean 仍在，只是不会被定时触发（测试里关闭）
 * @param interval       两轮之间的间隔（上一轮结束后开始计时，同一进程内不会重叠）
 * @param initialDelay   应用启动后第一轮的延迟
 * @param batchSize      每轮最多处理的问卷数（跨所有租户合计）
 * @param maxAttempts    同一 requestId 累计发送次数达到此值且结果仍未知时，停止自动重试并标记人工复核
 * @param backoffInitial 第一次结果未知后的等待时间；此后每次翻倍
 * @param backoffMax     退避上限
 */
@ConfigurationProperties("platform.survey.reconcile")
public record PublishReconciliationProperties(
        @DefaultValue("true") boolean enabled,
        @DefaultValue("PT1M") Duration interval,
        @DefaultValue("PT30S") Duration initialDelay,
        @DefaultValue("50") int batchSize,
        @DefaultValue("8") int maxAttempts,
        @DefaultValue("PT1M") Duration backoffInitial,
        @DefaultValue("PT30M") Duration backoffMax) {

    public PublishReconciliationProperties {
        requirePositive(interval, "interval");
        requirePositive(backoffInitial, "backoff-initial");
        requirePositive(backoffMax, "backoff-max");
        if (initialDelay == null || initialDelay.isNegative()) {
            throw new IllegalArgumentException("platform.survey.reconcile.initial-delay must not be negative");
        }
        if (batchSize < 1) {
            throw new IllegalArgumentException("platform.survey.reconcile.batch-size must be positive");
        }
        if (maxAttempts < 1) {
            throw new IllegalArgumentException("platform.survey.reconcile.max-attempts must be positive");
        }
        if (backoffMax.compareTo(backoffInitial) < 0) {
            throw new IllegalArgumentException("platform.survey.reconcile.backoff-max must be >= backoff-initial");
        }
    }

    private static void requirePositive(Duration value, String name) {
        if (value == null || value.isZero() || value.isNegative()) {
            throw new IllegalArgumentException("platform.survey.reconcile." + name + " must be positive");
        }
    }
}
