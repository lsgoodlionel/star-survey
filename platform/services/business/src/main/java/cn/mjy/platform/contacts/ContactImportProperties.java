package cn.mjy.platform.contacts;

import java.time.Duration;
import org.springframework.boot.context.properties.ConfigurationProperties;
import org.springframework.boot.context.properties.bind.DefaultValue;

/** 联系人导入作业的运行参数。{@code enabled=false} 时不装配定时器，测试直接驱动 worker。 */
@ConfigurationProperties("platform.contacts.import")
public record ContactImportProperties(
        @DefaultValue("true") boolean enabled,
        @DefaultValue("PT10S") Duration interval,
        @DefaultValue("PT20S") Duration initialDelay,
        @DefaultValue("PT5M") Duration lease,
        @DefaultValue("5") int maxAttempts,
        @DefaultValue("PT1M") Duration backoff,
        @DefaultValue("500") int maxRowsPerRun,
        @DefaultValue("50000") int maxRows,
        @DefaultValue("8388608") int maxContentBytes) {

    public ContactImportProperties {
        requirePositive(interval, "interval");
        requirePositive(lease, "lease");
        if (maxAttempts < 1 || maxRowsPerRun < 1 || maxRows < 1 || maxContentBytes < 1) {
            throw new IllegalArgumentException("platform.contacts.import counters must be positive");
        }
    }

    private static void requirePositive(Duration value, String name) {
        if (value == null || value.isNegative() || value.isZero()) {
            throw new IllegalArgumentException("platform.contacts.import." + name + " must be positive");
        }
    }
}
