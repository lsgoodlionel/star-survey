package cn.mjy.platform.entitlement;

import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.stereotype.Repository;

/**
 * 余额投影 usage_counter 的读写。所有方法都要求调用方已处于租户作用域的事务内：
 * tenant_id 取自 {@code app_current_tenant()}，不从参数传入，写错租户在结构上不可能。
 */
@Repository
class UsageCounters {

    private final JdbcClient jdbc;

    UsageCounters(JdbcClient jdbc) {
        this.jdbc = jdbc;
    }

    /** 已占用量（预占中 + 已结算）；没有记录时为 0。 */
    long used(CounterKey key) {
        return balance(key).used();
    }

    UsageBalance balance(CounterKey key) {
        return jdbc.sql("""
                SELECT reserved, captured FROM usage_counter
                WHERE tenant_id = app_current_tenant()
                  AND meter_code = :meter AND subject = :subject AND period_key = :period
                """)
                .param("meter", key.meter())
                .param("subject", key.subject())
                .param("period", key.periodKey())
                .query((rs, row) -> new UsageBalance(rs.getLong("reserved"), rs.getLong("captured")))
                .optional()
                .orElse(UsageBalance.EMPTY);
    }

    /**
     * 原子地预占 {@code quantity}：仅当占用后不超过 {@code limit} 时才更新并返回 true。
     * 并发下由行锁串行化，拿到锁后按最新行重新判断条件，因此永远不会超额。
     */
    boolean tryReserve(CounterKey key, long quantity, long limit) {
        ensureRow(key);
        int updated = jdbc.sql("""
                UPDATE usage_counter
                   SET reserved = reserved + :quantity, updated_at = now()
                 WHERE tenant_id = app_current_tenant()
                   AND meter_code = :meter AND subject = :subject AND period_key = :period
                   AND reserved + captured + :quantity <= :limit
                """)
                .param("quantity", quantity)
                .param("limit", limit)
                .param("meter", key.meter())
                .param("subject", key.subject())
                .param("period", key.periodKey())
                .update();
        return updated == 1;
    }

    /** 预占转结算：预占中减去 {@code reserved}，已结算加上 {@code captured}（可少于预占量）。 */
    void capture(CounterKey key, long reserved, long captured) {
        adjust(key, -reserved, captured);
    }

    /** 释放或过期回收：预占中减去 {@code reserved}。 */
    void releaseReserved(CounterKey key, long reserved) {
        adjust(key, -reserved, 0);
    }

    private void adjust(CounterKey key, long reservedDelta, long capturedDelta) {
        int updated = jdbc.sql("""
                UPDATE usage_counter
                   SET reserved = reserved + :reservedDelta, captured = captured + :capturedDelta,
                       updated_at = now()
                 WHERE tenant_id = app_current_tenant()
                   AND meter_code = :meter AND subject = :subject AND period_key = :period
                """)
                .param("reservedDelta", reservedDelta)
                .param("capturedDelta", capturedDelta)
                .param("meter", key.meter())
                .param("subject", key.subject())
                .param("period", key.periodKey())
                .update();
        if (updated != 1) {
            throw new IllegalStateException("usage counter missing for " + key);
        }
    }

    private void ensureRow(CounterKey key) {
        jdbc.sql("""
                INSERT INTO usage_counter (tenant_id, meter_code, subject, period_key)
                VALUES (app_current_tenant(), :meter, :subject, :period)
                ON CONFLICT DO NOTHING
                """)
                .param("meter", key.meter())
                .param("subject", key.subject())
                .param("period", key.periodKey())
                .update();
    }
}
