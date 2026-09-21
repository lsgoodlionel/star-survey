package cn.mjy.platform.entitlement;

import static org.assertj.core.api.Assertions.assertThat;

import cn.mjy.platform.shared.TenantContext;
import cn.mjy.platform.shared.TenantId;
import cn.mjy.platform.shared.tenant.TenantScope;
import java.time.Duration;
import java.util.ArrayList;
import java.util.List;
import java.util.concurrent.Callable;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.Future;
import java.util.concurrent.TimeUnit;
import java.util.function.IntFunction;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.jdbc.core.simple.JdbcClient;

/**
 * 并发证据：真实 PostgreSQL 上 50 个线程同时抢最后几个单位，授予量必须恰好等于剩余额度。
 *
 * P0 实测引擎自带的名额控制在类似竞争下会超发（8 个名额放进了 6～7 个抢最后一个名额的请求）；
 * 这里的预占用条件更新在行锁上串行化，任何一轮都不允许多给一个。
 * 连接池放大到 32，使大多数线程真正同时持有连接进入数据库竞争。
 */
@SpringBootTest(properties = "spring.datasource.hikari.maximum-pool-size=32")
class UsageLedgerConcurrencyTest {

    private static final int THREADS = 50;
    private static final int ROUNDS = 10;
    private static final long QUOTA = 100;
    private static final long REMAINING = 5;
    private static final Duration TTL = Duration.ofMinutes(10);

    @Autowired
    private UsageLedger ledger;

    @Autowired
    private EntitlementFixtures fixtures;

    @Autowired
    private TenantScope tenantScope;

    @Autowired
    private JdbcClient jdbc;

    @Test
    void fiftyThreadsRacingForTheLastUnitsAreGrantedExactlyTheRemainingQuota() throws Exception {
        for (int round = 0; round < ROUNDS; round++) {
            TenantId tenant = fixtures.activeTenantWithQuota(QUOTA);
            TenantContext ctx = EntitlementFixtures.contextOf(tenant);
            ledger.reserve(ctx, tokens("warm-up", QUOTA - REMAINING));
            ledger.capture(ctx, "warm-up", QUOTA - REMAINING);

            List<ReservationResult> results = race(i -> () -> ledger.reserve(ctx, tokens("racer-" + i, 1)));

            long granted = results.stream().filter(ReservationResult::isGranted).count();
            assertThat(granted).as("round %d granted", round).isEqualTo(REMAINING);
            assertThat(results).filteredOn(r -> !r.isGranted())
                    .allSatisfy(r -> assertThat(r.decision())
                            .isEqualTo(Decision.needUpgrade(DecisionReason.QUOTA_EXCEEDED)));
            assertThat(ledger.balance(ctx, Meters.AI_TOKENS, null))
                    .isEqualTo(new UsageBalance(REMAINING, QUOTA - REMAINING));
            assertThat(reservedInLedger(tenant)).isEqualTo(QUOTA);
        }
    }

    @Test
    void fiftyConcurrentRetriesOfOneKeyReserveOnce() throws Exception {
        TenantId tenant = fixtures.activeTenantWithQuota(QUOTA);
        TenantContext ctx = EntitlementFixtures.contextOf(tenant);

        List<ReservationResult> results = race(i -> () -> ledger.reserve(ctx, tokens("same-key", 7)));

        assertThat(results).allMatch(ReservationResult::isGranted);
        assertThat(results).filteredOn(r -> !r.replayed()).hasSize(1);
        assertThat(ledger.balance(ctx, Meters.AI_TOKENS, null)).isEqualTo(new UsageBalance(7, 0));
        assertThat(reservedInLedger(tenant)).isEqualTo(7);
    }

    @Test
    void racingCapturesAndReleasesSettleAReservationExactlyOnce() throws Exception {
        TenantId tenant = fixtures.activeTenantWithQuota(QUOTA);
        TenantContext ctx = EntitlementFixtures.contextOf(tenant);
        ledger.reserve(ctx, tokens("contested", 9));

        List<SettlementResult> results = race(i -> () -> i % 2 == 0
                ? ledger.capture(ctx, "contested", 9)
                : ledger.release(ctx, "contested"));

        long firstApplications = results.stream().filter(r -> r.isApplied() && !r.replayed()).count();
        assertThat(firstApplications).isEqualTo(1);
        assertThat(settlementsInLedger(tenant)).isEqualTo(1);
        UsageBalance balance = ledger.balance(ctx, Meters.AI_TOKENS, null);
        assertThat(balance.reserved()).isZero();
        assertThat(balance.captured()).isIn(0L, 9L);
    }

    @Test
    void racingReclaimersReclaimAnExpiredReservationExactlyOnce() throws Exception {
        TenantId tenant = fixtures.activeTenantWithQuota(QUOTA);
        TenantContext ctx = EntitlementFixtures.contextOf(tenant);
        ledger.reserve(ctx, new ReservationRequest("stale", Capabilities.AI_SURVEY_GENERATE, null, 9,
                null, Duration.ofMillis(1)));
        Thread.sleep(50);

        List<Integer> reclaimed = race(i -> () -> ledger.reclaimExpired(tenant));

        assertThat(reclaimed.stream().mapToInt(Integer::intValue).sum()).isEqualTo(1);
        assertThat(ledger.balance(ctx, Meters.AI_TOKENS, null)).isEqualTo(UsageBalance.EMPTY);
    }

    /** 所有线程在同一道门后就位，一起放行，最大化竞争。 */
    private static <T> List<T> race(IntFunction<Callable<T>> task) throws Exception {
        ExecutorService pool = Executors.newFixedThreadPool(THREADS);
        try {
            CountDownLatch ready = new CountDownLatch(THREADS);
            CountDownLatch go = new CountDownLatch(1);
            List<Future<T>> futures = new ArrayList<>();
            for (int i = 0; i < THREADS; i++) {
                Callable<T> work = task.apply(i);
                futures.add(pool.submit(() -> {
                    ready.countDown();
                    go.await();
                    return work.call();
                }));
            }
            ready.await(10, TimeUnit.SECONDS);
            go.countDown();
            List<T> results = new ArrayList<>();
            for (Future<T> future : futures) {
                results.add(future.get(60, TimeUnit.SECONDS));
            }
            return results;
        } finally {
            pool.shutdownNow();
        }
    }

    private static ReservationRequest tokens(String key, long quantity) {
        return new ReservationRequest(key, Capabilities.AI_SURVEY_GENERATE, null, quantity, "model-x", TTL);
    }

    private long reservedInLedger(TenantId tenant) {
        return tenantScope.call(tenant, () -> jdbc.sql(
                        "SELECT COALESCE(SUM(quantity), 0) FROM usage_ledger_entry WHERE kind = 'reserve'")
                .query(Long.class).single());
    }

    private long settlementsInLedger(TenantId tenant) {
        return tenantScope.call(tenant, () -> jdbc.sql(
                        "SELECT COUNT(*) FROM usage_ledger_entry WHERE kind <> 'reserve'")
                .query(Long.class).single());
    }
}
