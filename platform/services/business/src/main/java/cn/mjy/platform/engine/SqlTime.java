package cn.mjy.platform.engine;

import java.time.Instant;
import java.time.OffsetDateTime;
import java.time.ZoneOffset;

/** PostgreSQL JDBC 驱动不直接接受 {@link Instant}，统一转成 UTC 的 {@link OffsetDateTime} 写入 timestamptz。 */
final class SqlTime {

    private SqlTime() {
    }

    static OffsetDateTime utc(Instant instant) {
        return instant == null ? null : OffsetDateTime.ofInstant(instant, ZoneOffset.UTC);
    }
}
