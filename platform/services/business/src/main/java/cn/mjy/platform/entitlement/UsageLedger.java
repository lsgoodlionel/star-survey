package cn.mjy.platform.entitlement;

import cn.mjy.platform.entitlement.EntitlementEvaluator.Evaluation;
import cn.mjy.platform.entitlement.LedgerEntries.EntryKind;
import cn.mjy.platform.entitlement.LedgerEntries.LedgerActor;
import cn.mjy.platform.entitlement.LedgerEntries.ReserveEntry;
import cn.mjy.platform.entitlement.LedgerEntries.Settlement;
import cn.mjy.platform.shared.TenantContext;
import cn.mjy.platform.shared.TenantId;
import cn.mjy.platform.shared.tenant.TenantScope;
import java.time.Instant;
import java.time.temporal.ChronoUnit;
import java.util.Comparator;
import java.util.List;
import java.util.Objects;
import org.springframework.stereotype.Service;

/**
 * 用量账本：预占（reserve）-> 结算（capture）| 释放（release），过期未结算的预占可回收（reclaim）。
 *
 * <ul>
 *   <li>每个操作都在一个租户作用域事务里完成：重新判定额度、写流水、改余额，要么全成要么全不成。</li>
 *   <li>幂等：同一幂等键重试预占只返回原结果；结算与释放重试同理。</li>
 *   <li>并发安全：余额用条件更新原子预占，并发下授予总量永不超过额度。</li>
 *   <li>流水只追加：运行期账号对流水表没有 UPDATE/DELETE 权限。</li>
 * </ul>
 */
@Service
public class UsageLedger {

    /** 系统回收任务在流水里的操作者标识。 */
    static final String RECLAIMER_ACTOR = "system:usage-reclaimer";
    private static final int RECLAIM_BATCH_SIZE = 1000;

    private final TenantScope tenantScope;
    private final EntitlementEvaluator evaluator;
    private final LedgerEntries entries;
    private final UsageCounters counters;
    private final PlanCatalog catalog;
    private final SubscriptionService subscriptions;

    public UsageLedger(TenantScope tenantScope, EntitlementEvaluator evaluator, LedgerEntries entries,
                       UsageCounters counters, PlanCatalog catalog, SubscriptionService subscriptions) {
        this.tenantScope = tenantScope;
        this.evaluator = evaluator;
        this.entries = entries;
        this.counters = counters;
        this.catalog = catalog;
        this.subscriptions = subscriptions;
    }

    public ReservationResult reserve(TenantContext context, ReservationRequest request) {
        return reserveAs(context.tenantId(), actorOf(context), request);
    }

    /** 结算实际用量（不超过预占量）；多余部分随之释放。 */
    public SettlementResult capture(TenantContext context, String idempotencyKey, long actualQuantity) {
        return captureAs(context.tenantId(), actorOf(context), idempotencyKey, actualQuantity);
    }

    public SettlementResult release(TenantContext context, String idempotencyKey) {
        return releaseAs(context.tenantId(), actorOf(context), idempotencyKey);
    }

    /** 回收本租户已过有效期、仍未结算的预占，返回本次回收条数。每个预占只会被回收一次。 */
    public int reclaimExpired(TenantId tenant) {
        LedgerActor actor = new LedgerActor(RECLAIMER_ACTOR, null);
        return tenantScope.call(tenant, () -> {
            List<Settlement.Expired> expired = entries.expireOverdue(Instant.now(), actor, RECLAIM_BATCH_SIZE);
            // 按余额位置排序后逐个回减，并发回收之间的加锁顺序一致，避免死锁。
            expired.stream()
                    .sorted(Comparator.comparing((Settlement.Expired e) -> e.counterKey().toString()))
                    .forEach(e -> counters.releaseReserved(e.counterKey(), e.quantity()));
            return expired.size();
        });
    }

    /**
     * 某计量当前周期的余额。{@code subject}：按问卷累计的计量填问卷标识，按租户累计的填 null。
     * 按订阅期归零的计量取当前订阅期；没有订阅时为空余额。
     */
    public UsageBalance balance(TenantContext context, String meterCode, String subject) {
        MeterDefinition meter = catalog.meter(meterCode)
                .orElseThrow(() -> new IllegalArgumentException("unknown meter: " + meterCode));
        return tenantScope.call(context.tenantId(), () -> subscriptions.currentInScope(Instant.now())
                .map(subscription -> counters.balance(CounterKey.of(meter, subject, subscription)))
                .orElse(UsageBalance.EMPTY));
    }

    ReservationResult reserveAs(TenantId tenant, LedgerActor actor, ReservationRequest request) {
        try {
            return tenantScope.call(tenant, () -> reserveInScope(actor, request));
        } catch (QuotaRaceLostException lost) {
            return ReservationResult.rejected(request.idempotencyKey(),
                    Decision.needUpgrade(DecisionReason.QUOTA_EXCEEDED));
        }
    }

    SettlementResult captureAs(TenantId tenant, LedgerActor actor, String key, long actualQuantity) {
        ReservationRequest.requireKey(key);
        if (actualQuantity <= 0) {
            throw new IllegalArgumentException("captured quantity must be positive; release instead");
        }
        return tenantScope.call(tenant, () -> entries.findReserve(key)
                .map(reserve -> captureInScope(reserve, actor, actualQuantity))
                .orElse(SettlementResult.rejected(SettlementOutcome.NOT_FOUND)));
    }

    SettlementResult releaseAs(TenantId tenant, LedgerActor actor, String key) {
        ReservationRequest.requireKey(key);
        return tenantScope.call(tenant, () -> entries.findReserve(key)
                .map(reserve -> releaseInScope(reserve, actor))
                .orElse(SettlementResult.rejected(SettlementOutcome.NOT_FOUND)));
    }

    private ReservationResult reserveInScope(LedgerActor actor, ReservationRequest request) {
        String key = request.idempotencyKey();
        var existing = entries.findReserve(key);
        if (existing.isPresent()) {
            return replayReservation(existing.get(), request);
        }
        Instant now = Instant.now();
        Evaluation evaluation = evaluator.evaluate(request.capability(), request.subject(), request.quantity(), now);
        if (evaluation.capability() != null && !evaluation.capability().isMetered()) {
            throw new IllegalArgumentException("capability " + request.capability() + " is not metered");
        }
        if (!evaluation.decision().isAllowed()) {
            return ReservationResult.rejected(key, evaluation.decision());
        }
        ReserveEntry entry = new ReserveEntry(key, evaluation.subscription().id(), evaluation.counterKey(),
                request.capability(), request.model(), request.quantity(),
                now.plus(request.ttl()).truncatedTo(ChronoUnit.MICROS));
        if (!entries.insertReserve(entry, actor)) {
            // 并发的同键请求先一步提交：按重放处理。
            return replayReservation(entries.findReserve(key).orElseThrow(), request);
        }
        if (!counters.tryReserve(entry.counterKey(), entry.quantity(), evaluation.limit())) {
            // 判定之后被并发预占抢走了余量：抛出以回滚刚写入的流水。
            throw new QuotaRaceLostException();
        }
        return ReservationResult.granted(key, entry.quantity(), entry.expiresAt(), false);
    }

    private static ReservationResult replayReservation(ReserveEntry existing, ReservationRequest request) {
        String subject = existing.counterKey().subject();
        boolean sameSubject = subject.equals(CounterKey.TENANT_SUBJECT) || subject.equals(request.subject());
        if (!existing.capability().equals(request.capability()) || existing.quantity() != request.quantity()
                || !Objects.equals(existing.model(), request.model()) || !sameSubject) {
            throw new IdempotencyKeyConflictException(existing.key(),
                    "originally " + existing.capability() + " x " + existing.quantity());
        }
        return ReservationResult.granted(existing.key(), existing.quantity(), existing.expiresAt(), true);
    }

    private SettlementResult captureInScope(ReserveEntry reserve, LedgerActor actor, long actualQuantity) {
        var settled = entries.findSettlement(reserve.key());
        if (settled.isPresent()) {
            return replayOrReject(settled.get(), EntryKind.CAPTURE, actualQuantity, reserve.key());
        }
        if (actualQuantity > reserve.quantity()) {
            return SettlementResult.rejected(SettlementOutcome.QUANTITY_EXCEEDS_RESERVATION);
        }
        if (!Instant.now().isBefore(reserve.expiresAt())) {
            return SettlementResult.rejected(SettlementOutcome.RESERVATION_EXPIRED);
        }
        if (!entries.insertSettlement(reserve, EntryKind.CAPTURE, actualQuantity, actor)) {
            return replayOrReject(entries.findSettlement(reserve.key()).orElseThrow(),
                    EntryKind.CAPTURE, actualQuantity, reserve.key());
        }
        counters.capture(reserve.counterKey(), reserve.quantity(), actualQuantity);
        return SettlementResult.applied(SettlementOutcome.CAPTURED, actualQuantity, false);
    }

    private SettlementResult releaseInScope(ReserveEntry reserve, LedgerActor actor) {
        var settled = entries.findSettlement(reserve.key());
        if (settled.isPresent()) {
            return replayOrReject(settled.get(), EntryKind.RELEASE, reserve.quantity(), reserve.key());
        }
        if (!entries.insertSettlement(reserve, EntryKind.RELEASE, reserve.quantity(), actor)) {
            return replayOrReject(entries.findSettlement(reserve.key()).orElseThrow(),
                    EntryKind.RELEASE, reserve.quantity(), reserve.key());
        }
        counters.releaseReserved(reserve.counterKey(), reserve.quantity());
        return SettlementResult.applied(SettlementOutcome.RELEASED, reserve.quantity(), false);
    }

    /** 已有结算类流水：同种操作且数量一致视为幂等重放，否则按已有终态拒绝。 */
    private static SettlementResult replayOrReject(Settlement existing, EntryKind requested, long quantity,
                                                   String key) {
        if (existing.kind() == requested) {
            if (existing.quantity() != quantity) {
                throw new IdempotencyKeyConflictException(key,
                        "already " + existing.kind() + " x " + existing.quantity());
            }
            SettlementOutcome outcome = requested == EntryKind.CAPTURE
                    ? SettlementOutcome.CAPTURED : SettlementOutcome.RELEASED;
            return SettlementResult.applied(outcome, quantity, true);
        }
        return SettlementResult.rejected(switch (existing.kind()) {
            case CAPTURE -> SettlementOutcome.ALREADY_CAPTURED;
            case RELEASE -> SettlementOutcome.ALREADY_RELEASED;
            case EXPIRE -> SettlementOutcome.ALREADY_EXPIRED;
            case RESERVE -> throw new IllegalStateException("reserve is not a settlement");
        });
    }

    private static LedgerActor actorOf(TenantContext context) {
        return new LedgerActor(context.actorId(), context.traceId());
    }

    /** 事务内用来触发回滚的信号：判定后余量被并发预占抢走。 */
    private static final class QuotaRaceLostException extends RuntimeException {

        QuotaRaceLostException() {
            super("quota taken by a concurrent reservation", null, false, false);
        }
    }
}
