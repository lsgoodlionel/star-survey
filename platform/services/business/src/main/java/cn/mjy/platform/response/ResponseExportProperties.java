package cn.mjy.platform.response;

import java.nio.file.Path;
import java.time.Duration;
import org.springframework.boot.context.properties.ConfigurationProperties;
import org.springframework.boot.context.properties.bind.DefaultValue;

/**
 * 答卷导出作业的配置（前缀 {@code platform.response.export}，ADR 0015）。
 *
 * @param enabled          是否启用定时调度；关闭时 {@link ResponseExportWorker} 仍在，只是不被定时触发（测试里关闭）
 * @param interval         两轮之间的间隔（上一轮结束后开始计时）
 * @param initialDelay     启动后第一轮的延迟
 * @param storageDir       本地文件存储根目录（开发与测试；生产换对象存储实现）；为空取系统临时目录下的 mjy-exports
 * @param retention        作业创建到文件到期的时长，创建时即作为 expiresAt 返回
 * @param batchSize        每批导出的答卷数，不超过网关单次读取上限 500
 * @param snapshotPageSize 物化快照时每页读取的投影行数
 * @param lease            执行者租约时长；每批续租，进程死掉后超过此时长由别人接手
 * @param maxAttempts      单个作业累计失败次数上限，达到即 failed
 * @param backoff          失败后到下一次重试的等待
 * @param maxStepsPerRun   定时一轮里每个作业最多推进的步数，之后让出给其他作业
 */
@ConfigurationProperties("platform.response.export")
public record ResponseExportProperties(
        @DefaultValue("true") boolean enabled,
        @DefaultValue("PT10S") Duration interval,
        @DefaultValue("PT20S") Duration initialDelay,
        @DefaultValue("") String storageDir,
        @DefaultValue("PT72H") Duration retention,
        @DefaultValue("500") int batchSize,
        @DefaultValue("5000") int snapshotPageSize,
        @DefaultValue("PT5M") Duration lease,
        @DefaultValue("5") int maxAttempts,
        @DefaultValue("PT1M") Duration backoff,
        @DefaultValue("200") int maxStepsPerRun) {

    /** 网关读取端点单次最多 500 个答卷号（契约 response-read-v1）。 */
    public static final int MAX_BATCH_SIZE = 500;

    private static final String DEFAULT_DIR_NAME = "mjy-exports";

    public ResponseExportProperties {
        requirePositive(interval, "interval");
        requirePositive(retention, "retention");
        requirePositive(lease, "lease");
        if (initialDelay == null || initialDelay.isNegative() || backoff == null || backoff.isNegative()) {
            throw new IllegalArgumentException("platform.response.export delays must not be negative");
        }
        if (batchSize < 1 || batchSize > MAX_BATCH_SIZE) {
            throw new IllegalArgumentException("platform.response.export.batch-size must be 1.." + MAX_BATCH_SIZE);
        }
        if (snapshotPageSize < 1 || maxAttempts < 1 || maxStepsPerRun < 1) {
            throw new IllegalArgumentException(
                    "platform.response.export snapshot-page-size, max-attempts and max-steps-per-run must be positive");
        }
    }

    Path storageRoot() {
        return storageDir == null || storageDir.isBlank()
                ? Path.of(System.getProperty("java.io.tmpdir"), DEFAULT_DIR_NAME)
                : Path.of(storageDir);
    }

    private static void requirePositive(Duration value, String name) {
        if (value == null || value.isZero() || value.isNegative()) {
            throw new IllegalArgumentException("platform.response.export." + name + " must be positive");
        }
    }
}
