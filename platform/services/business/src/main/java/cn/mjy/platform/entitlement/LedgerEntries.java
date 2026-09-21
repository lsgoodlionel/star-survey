package cn.mjy.platform.entitlement;

import java.sql.ResultSet;
import java.sql.SQLException;
import java.sql.Types;
import java.time.Instant;
import java.time.OffsetDateTime;
import java.time.ZoneOffset;
import java.util.List;
import java.util.Optional;
import java.util.UUID;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.stereotype.Repository;

/**
 * 只追加的流水表 usage_ledger_entry。调用方必须已处于租户作用域的事务内；
 * tenant_id 一律取 {@code app_current_tenant()}。
 *
 * "每个键至多一条 reserve、至多一条结算类流水"由部分唯一索引保证，这里用
 * {@code ON CONFLICT DO NOTHING} 探测是否首次写入：并发的同键请求在唯一索引上排队，
 * 先提交者生效，后到者得到"未插入"，再读取已有流水决定重放或拒绝。
 */
@Repository
class LedgerEntries {

    private static final String RESERVE_COLUMNS = """
            reservation_key, subscription_id, meter_code, subject, period_key,
            capability_code, model, quantity, expires_at
            """;

    private final JdbcClient jdbc;

    LedgerEntries(JdbcClient jdbc) {
        this.jdbc = jdbc;
    }

    /** 写入预占流水；该键已有预占时不写入并返回 false。 */
    boolean insertReserve(ReserveEntry entry, LedgerActor actor) {
        int inserted = jdbc.sql("""
                INSERT INTO usage_ledger_entry (tenant_id, reservation_key, kind, subscription_id, meter_code,
                       subject, period_key, capability_code, model, quantity, expires_at, actor_id, trace_id)
                VALUES (app_current_tenant(), :key, 'reserve', :subscription, :meter,
                       :subject, :period, :capability, :model, :quantity, :expiresAt, :actor, :trace)
                ON CONFLICT (tenant_id, reservation_key) WHERE kind = 'reserve' DO NOTHING
                """)
                .param("key", entry.key())
                .param("subscription", entry.subscriptionId())
                .param("meter", entry.counterKey().meter())
                .param("subject", entry.counterKey().subject())
                .param("period", entry.counterKey().periodKey())
                .param("capability", entry.capability())
                .param("model", entry.model(), Types.VARCHAR)
                .param("quantity", entry.quantity())
                .param("expiresAt", OffsetDateTime.ofInstant(entry.expiresAt(), ZoneOffset.UTC))
                .param("actor", actor.actorId())
                .param("trace", actor.traceId(), Types.VARCHAR)
                .update();
        return inserted == 1;
    }

    Optional<ReserveEntry> findReserve(String key) {
        return jdbc.sql("SELECT " + RESERVE_COLUMNS + """
                  FROM usage_ledger_entry
                 WHERE tenant_id = app_current_tenant() AND reservation_key = :key AND kind = 'reserve'
                """)
                .param("key", key)
                .query(LedgerEntries::mapReserve)
                .optional();
    }

    /** 写入结算类流水（capture/release/expire）；该键已结算过时不写入并返回 false。 */
    boolean insertSettlement(ReserveEntry reserve, EntryKind kind, long quantity, LedgerActor actor) {
        int inserted = jdbc.sql("""
                INSERT INTO usage_ledger_entry (tenant_id, reservation_key, kind, subscription_id, meter_code,
                       subject, period_key, capability_code, model, quantity, actor_id, trace_id)
                VALUES (app_current_tenant(), :key, :kind, :subscription, :meter,
                       :subject, :period, :capability, :model, :quantity, :actor, :trace)
                ON CONFLICT (tenant_id, reservation_key) WHERE kind <> 'reserve' DO NOTHING
                """)
                .param("key", reserve.key())
                .param("kind", CatalogEnums.toDatabase(kind))
                .param("subscription", reserve.subscriptionId())
                .param("meter", reserve.counterKey().meter())
                .param("subject", reserve.counterKey().subject())
                .param("period", reserve.counterKey().periodKey())
                .param("capability", reserve.capability())
                .param("model", reserve.model(), Types.VARCHAR)
                .param("quantity", quantity)
                .param("actor", actor.actorId())
                .param("trace", actor.traceId(), Types.VARCHAR)
                .update();
        return inserted == 1;
    }

    Optional<Settlement> findSettlement(String key) {
        return jdbc.sql("""
                SELECT kind, quantity FROM usage_ledger_entry
                 WHERE tenant_id = app_current_tenant() AND reservation_key = :key AND kind <> 'reserve'
                """)
                .param("key", key)
                .query((rs, row) -> new Settlement(
                        CatalogEnums.fromDatabase(EntryKind.class, rs.getString("kind")), rs.getLong("quantity")))
                .optional();
    }

    /**
     * 为到期未结算的预占写入 expire 流水，返回本次真正写入的那些（用于回减余额）。
     * 并发回收或与结算竞争时，唯一索引保证每个预占只会有一条结算类流水，因此只回收一次。
     */
    List<Settlement.Expired> expireOverdue(Instant asOf, LedgerActor actor, int batchSize) {
        return jdbc.sql("""
                INSERT INTO usage_ledger_entry (tenant_id, reservation_key, kind, subscription_id, meter_code,
                       subject, period_key, capability_code, model, quantity, actor_id, trace_id)
                SELECT r.tenant_id, r.reservation_key, 'expire', r.subscription_id, r.meter_code,
                       r.subject, r.period_key, r.capability_code, r.model, r.quantity, :actor, :trace
                  FROM usage_ledger_entry r
                 WHERE r.tenant_id = app_current_tenant() AND r.kind = 'reserve' AND r.expires_at <= :asOf
                   AND NOT EXISTS (SELECT 1 FROM usage_ledger_entry s
                                    WHERE s.tenant_id = r.tenant_id AND s.reservation_key = r.reservation_key
                                      AND s.kind <> 'reserve')
                 ORDER BY r.expires_at, r.id
                 LIMIT :batchSize
                ON CONFLICT (tenant_id, reservation_key) WHERE kind <> 'reserve' DO NOTHING
                RETURNING meter_code, subject, period_key, quantity
                """)
                .param("asOf", OffsetDateTime.ofInstant(asOf, ZoneOffset.UTC))
                .param("actor", actor.actorId())
                .param("trace", actor.traceId(), Types.VARCHAR)
                .param("batchSize", batchSize)
                .query((rs, row) -> new Settlement.Expired(
                        new CounterKey(rs.getString("meter_code"), rs.getString("subject"), rs.getString("period_key")),
                        rs.getLong("quantity")))
                .list();
    }

    private static ReserveEntry mapReserve(ResultSet rs, int row) throws SQLException {
        return new ReserveEntry(rs.getString("reservation_key"),
                rs.getObject("subscription_id", UUID.class),
                new CounterKey(rs.getString("meter_code"), rs.getString("subject"), rs.getString("period_key")),
                rs.getString("capability_code"), rs.getString("model"), rs.getLong("quantity"),
                rs.getObject("expires_at", OffsetDateTime.class).toInstant());
    }

    /** 流水种类（落库值为小写）。 */
    enum EntryKind {
        RESERVE, CAPTURE, RELEASE, EXPIRE
    }

    /** 一条预占流水。 */
    record ReserveEntry(String key, UUID subscriptionId, CounterKey counterKey, String capability, String model,
                        long quantity, Instant expiresAt) {
    }

    /** 一条结算类流水。 */
    record Settlement(EntryKind kind, long quantity) {

        /** 回收写入的 expire 流水对应的余额位置与数量。 */
        record Expired(CounterKey counterKey, long quantity) {
        }
    }

    /** 流水的操作者与追踪号。 */
    record LedgerActor(String actorId, String traceId) {
    }
}
