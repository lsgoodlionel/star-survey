package cn.mjy.platform.delivery;

import cn.mjy.platform.shared.TenantId;
import java.time.Clock;
import java.time.Instant;
import java.time.OffsetDateTime;
import java.time.ZoneOffset;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.stereotype.Component;

/**
 * 每租户每渠道的发送限速（固定窗口）。必须在 {@code TenantScope} 内调用。
 *
 * <p>计数落在数据库而不是进程内存：多副本共用同一个窗口行，一起受限。
 * 取名额用一条带条件的 {@code INSERT … ON CONFLICT DO UPDATE … WHERE used &lt; 上限 RETURNING}：
 * 并发的取名额被行锁串行化，超额时不返回任何行，因此不会超发。
 *
 * <p>固定窗口而不是令牌桶：实现简单、行为可预测；代价是窗口边界处可能出现两倍瞬时速率，
 * 对"别把渠道商打爆"这个目的够用。
 */
@Component
class RateLimiter {

    private final JdbcClient jdbc;
    private final DeliveryProperties properties;
    private final Clock clock;

    RateLimiter(JdbcClient jdbc, DeliveryProperties properties) {
        this.jdbc = jdbc;
        this.properties = properties;
        this.clock = Clock.systemUTC();
    }

    /** 取一个名额；超额时返回 false，调用方应当本轮停下并稍后再来。 */
    boolean tryAcquire(TenantId tenant, DeliveryChannel channel) {
        OffsetDateTime window = windowStart();
        Integer used = jdbc.sql("""
                        INSERT INTO delivery_rate_window (tenant_id, channel, window_start, used)
                        VALUES (:tenant, :channel, :window, 1)
                        ON CONFLICT (tenant_id, channel, window_start)
                        DO UPDATE SET used = delivery_rate_window.used + 1
                        WHERE delivery_rate_window.used < :limit
                        RETURNING used
                        """)
                .param("tenant", tenant.value())
                .param("channel", channel.code())
                .param("window", window)
                .param("limit", properties.ratePerWindow())
                .query(Integer.class)
                .optional()
                .orElse(null);
        return used != null;
    }

    /** 窗口起点：把当前时刻按窗口长度向下取整，同一窗口内的请求落在同一行。 */
    private OffsetDateTime windowStart() {
        Instant now = clock.instant();
        long seconds = properties.rateWindow().toSeconds();
        return OffsetDateTime.ofInstant(Instant.ofEpochSecond(now.getEpochSecond() / seconds * seconds),
                ZoneOffset.UTC);
    }
}
