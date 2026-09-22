package cn.mjy.platform.survey;

import java.time.Duration;
import org.springframework.boot.context.properties.ConfigurationProperties;
import org.springframework.boot.context.properties.bind.DefaultValue;

/**
 * 在线版本的定时漂移巡检（前缀 {@code platform.survey.drift-check}，ADR 0012 决定 5）。
 *
 * @param enabled      是否启用定时巡检；默认关闭（每次巡检都会读引擎），测试里也关闭。按需检查不受影响
 * @param interval     两轮之间的间隔（上一轮结束后开始计时）
 * @param initialDelay 应用启动后第一轮的延迟
 * @param recheckAfter 同一在线版本两次巡检之间至少相隔多久
 * @param batchSize    每轮最多检查的版本数（跨所有租户合计）
 */
@ConfigurationProperties("platform.survey.drift-check")
public record DriftMonitorProperties(
        @DefaultValue("false") boolean enabled,
        @DefaultValue("PT15M") Duration interval,
        @DefaultValue("PT2M") Duration initialDelay,
        @DefaultValue("PT6H") Duration recheckAfter,
        @DefaultValue("100") int batchSize) {

    public DriftMonitorProperties {
        if (interval == null || interval.isZero() || interval.isNegative()) {
            throw new IllegalArgumentException("platform.survey.drift-check.interval must be positive");
        }
        if (recheckAfter == null || recheckAfter.isNegative()) {
            throw new IllegalArgumentException("platform.survey.drift-check.recheck-after must not be negative");
        }
        if (initialDelay == null || initialDelay.isNegative()) {
            throw new IllegalArgumentException("platform.survey.drift-check.initial-delay must not be negative");
        }
        if (batchSize < 1) {
            throw new IllegalArgumentException("platform.survey.drift-check.batch-size must be positive");
        }
    }
}
