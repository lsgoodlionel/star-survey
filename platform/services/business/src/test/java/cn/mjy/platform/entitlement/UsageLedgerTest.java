package cn.mjy.platform.entitlement;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import cn.mjy.platform.shared.TenantContext;
import cn.mjy.platform.shared.TenantId;
import cn.mjy.platform.shared.tenant.TenantScope;
import java.time.Duration;
import java.time.Instant;
import java.util.Map;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.jdbc.core.simple.JdbcClient;

/**
 * 用量账本：预占 -> 结算 | 释放，按幂等业务键。
 * 重试同一个键不会重复预占或重复结算；释放后结算、结算后释放都被拒绝；过期预占只回收一次。
 */
@SpringBootTest
class UsageLedgerTest {

    private static final Duration TTL = Duration.ofMinutes(10);

    @Autowired
    private UsageLedger ledger;

    @Autowired
    private EntitlementFixtures fixtures;

    @Autowired
    private SubscriptionService subscriptions;

    @Autowired
    private TenantScope tenantScope;

    @Autowired
    private JdbcClient jdbc;

    private TenantId tenant;
    private TenantContext ctx;

    @BeforeEach
    void activeTenantWithTenTokens() {
        tenant = fixtures.activeTenantWithQuota(10);
        ctx = EntitlementFixtures.contextOf(tenant);
    }

    @Test
    void reservingHoldsQuotaUntilCaptured() {
        ReservationResult reservation = ledger.reserve(ctx, aiTokens("job-1", 4));

        assertThat(reservation.isGranted()).isTrue();
        assertThat(reservation.replayed()).isFalse();
        assertThat(tokenBalance()).isEqualTo(new UsageBalance(4, 0));

        SettlementResult capture = ledger.capture(ctx, "job-1", 3);

        assertThat(capture.outcome()).isEqualTo(SettlementOutcome.CAPTURED);
        assertThat(tokenBalance()).isEqualTo(new UsageBalance(0, 3));
    }

    @Test
    void retryingTheSameReservationKeyNeverReservesTwice() {
        ReservationResult first = ledger.reserve(ctx, aiTokens("job-1", 4));
        ReservationResult retry = ledger.reserve(ctx, aiTokens("job-1", 4));

        assertThat(retry.isGranted()).isTrue();
        assertThat(retry.replayed()).isTrue();
        assertThat(retry.expiresAt()).isEqualTo(first.expiresAt());
        assertThat(tokenBalance()).isEqualTo(new UsageBalance(4, 0));
        assertThat(entryCount("reserve")).isEqualTo(1);
    }

    @Test
    void reusingAKeyForADifferentRequestIsAConflict() {
        ledger.reserve(ctx, aiTokens("job-1", 4));

        assertThatThrownBy(() -> ledger.reserve(ctx, aiTokens("job-1", 5)))
                .isInstanceOf(IdempotencyKeyConflictException.class);
    }

    @Test
    void reusingAKeyForAnotherSurveyIsAConflict() {
        ledger.reserve(ctx, new ReservationRequest("resp-1", Capabilities.RESPONSE_COLLECT, "survey-1", 1, null, TTL));

        assertThatThrownBy(() -> ledger.reserve(ctx,
                new ReservationRequest("resp-1", Capabilities.RESPONSE_COLLECT, "survey-2", 1, null, TTL)))
                .isInstanceOf(IdempotencyKeyConflictException.class);
    }

    @Test
    void retryingACaptureNeverCapturesTwice() {
        ledger.reserve(ctx, aiTokens("job-1", 4));
        ledger.capture(ctx, "job-1", 4);

        SettlementResult retry = ledger.capture(ctx, "job-1", 4);

        assertThat(retry.outcome()).isEqualTo(SettlementOutcome.CAPTURED);
        assertThat(retry.replayed()).isTrue();
        assertThat(tokenBalance()).isEqualTo(new UsageBalance(0, 4));
        assertThat(entryCount("capture")).isEqualTo(1);
    }

    @Test
    void captureAfterReleaseIsRejected() {
        ledger.reserve(ctx, aiTokens("job-1", 4));
        assertThat(ledger.release(ctx, "job-1").outcome()).isEqualTo(SettlementOutcome.RELEASED);

        SettlementResult capture = ledger.capture(ctx, "job-1", 4);

        assertThat(capture.outcome()).isEqualTo(SettlementOutcome.ALREADY_RELEASED);
        assertThat(tokenBalance()).isEqualTo(new UsageBalance(0, 0));
    }

    @Test
    void releaseAfterCaptureIsRejected() {
        ledger.reserve(ctx, aiTokens("job-1", 4));
        ledger.capture(ctx, "job-1", 4);

        SettlementResult release = ledger.release(ctx, "job-1");

        assertThat(release.outcome()).isEqualTo(SettlementOutcome.ALREADY_CAPTURED);
        assertThat(tokenBalance()).isEqualTo(new UsageBalance(0, 4));
    }

    @Test
    void retryingAReleaseIsIdempotent() {
        ledger.reserve(ctx, aiTokens("job-1", 4));
        ledger.release(ctx, "job-1");

        SettlementResult retry = ledger.release(ctx, "job-1");

        assertThat(retry.outcome()).isEqualTo(SettlementOutcome.RELEASED);
        assertThat(retry.replayed()).isTrue();
        assertThat(entryCount("release")).isEqualTo(1);
    }

    @Test
    void capturingMoreThanWasReservedIsRejected() {
        ledger.reserve(ctx, aiTokens("job-1", 4));

        assertThat(ledger.capture(ctx, "job-1", 5).outcome())
                .isEqualTo(SettlementOutcome.QUANTITY_EXCEEDS_RESERVATION);
        assertThat(tokenBalance()).isEqualTo(new UsageBalance(4, 0));
    }

    @Test
    void settlingAnUnknownKeyReportsNotFound() {
        assertThat(ledger.capture(ctx, "nope", 1).outcome()).isEqualTo(SettlementOutcome.NOT_FOUND);
        assertThat(ledger.release(ctx, "nope").outcome()).isEqualTo(SettlementOutcome.NOT_FOUND);
    }

    @Test
    void reservingBeyondTheQuotaNeedsAnUpgradeAndHoldsNothing() {
        ledger.reserve(ctx, aiTokens("job-1", 8));

        ReservationResult denied = ledger.reserve(ctx, aiTokens("job-2", 3));

        assertThat(denied.isGranted()).isFalse();
        assertThat(denied.decision()).isEqualTo(Decision.needUpgrade(DecisionReason.QUOTA_EXCEEDED));
        assertThat(tokenBalance()).isEqualTo(new UsageBalance(8, 0));
        assertThat(entryCount("reserve")).isEqualTo(1);
    }

    @Test
    void releasedQuotaCanBeReservedAgain() {
        ledger.reserve(ctx, aiTokens("job-1", 10));
        ledger.release(ctx, "job-1");

        assertThat(ledger.reserve(ctx, aiTokens("job-2", 10)).isGranted()).isTrue();
    }

    @Test
    void anExpiredSubscriptionCannotReserve() {
        TenantId expired = TenantId.random();
        PlanVersion plan = fixtures.publish(EntitlementFixtures.ALL_CAPABILITIES, Map.of(Meters.AI_TOKENS, 10L));
        Instant now = Instant.now();
        subscriptions.start(expired, plan.id(), SubscriptionKind.PAID,
                now.minus(Duration.ofDays(30)), now.minus(Duration.ofDays(1)));

        ReservationResult result = ledger.reserve(EntitlementFixtures.contextOf(expired), aiTokens("job-1", 1));

        assertThat(result.decision()).isEqualTo(Decision.deny(DecisionReason.SUBSCRIPTION_READ_ONLY));
    }

    @Test
    void unmeteredCapabilitiesCannotBeReserved() {
        assertThatThrownBy(() -> ledger.reserve(ctx,
                new ReservationRequest("job-1", Capabilities.SURVEY_WRITE, null, 1, null, TTL)))
                .isInstanceOf(IllegalArgumentException.class);
    }

    @Test
    void expiredReservationsAreReclaimedExactlyOnce() throws InterruptedException {
        ledger.reserve(ctx, new ReservationRequest("job-1", Capabilities.AI_SURVEY_GENERATE, null, 6,
                "model-x", Duration.ofMillis(1)));
        ledger.reserve(ctx, aiTokens("job-2", 2));
        Thread.sleep(50);

        assertThat(ledger.reclaimExpired(tenant)).isEqualTo(1);
        assertThat(ledger.reclaimExpired(tenant)).isZero();
        assertThat(tokenBalance()).isEqualTo(new UsageBalance(2, 0));
        assertThat(entryCount("expire")).isEqualTo(1);
        assertThat(ledger.capture(ctx, "job-1", 6).outcome()).isEqualTo(SettlementOutcome.ALREADY_EXPIRED);
        assertThat(ledger.release(ctx, "job-1").outcome()).isEqualTo(SettlementOutcome.ALREADY_EXPIRED);
    }

    @Test
    void anExpiredButNotYetReclaimedReservationCannotBeCaptured() throws InterruptedException {
        ledger.reserve(ctx, new ReservationRequest("job-1", Capabilities.AI_SURVEY_GENERATE, null, 6,
                null, Duration.ofMillis(1)));
        Thread.sleep(50);

        assertThat(ledger.capture(ctx, "job-1", 6).outcome()).isEqualTo(SettlementOutcome.RESERVATION_EXPIRED);
        assertThat(ledger.reclaimExpired(tenant)).isEqualTo(1);
        assertThat(tokenBalance()).isEqualTo(UsageBalance.EMPTY);
    }

    @Test
    void ledgerEntriesRecordCapabilityModelAndActorForPricing() {
        ledger.reserve(ctx, aiTokens("job-1", 4));
        ledger.capture(ctx, "job-1", 3);

        Map<String, Object> capture = tenantScope.call(tenant, () -> jdbc.sql("""
                SELECT capability_code, model, meter_code, quantity, actor_id
                FROM usage_ledger_entry WHERE reservation_key = 'job-1' AND kind = 'capture'
                """).query().singleRow());

        assertThat(capture).containsEntry("capability_code", Capabilities.AI_SURVEY_GENERATE)
                .containsEntry("model", "model-x")
                .containsEntry("meter_code", Meters.AI_TOKENS)
                .containsEntry("quantity", 3L)
                .containsEntry("actor_id", ctx.actorId());
    }

    @Test
    void theLedgerIsAppendOnlyForTheRuntimeRole() {
        ledger.reserve(ctx, aiTokens("job-1", 4));

        assertThatThrownBy(() -> tenantScope.run(tenant,
                () -> jdbc.sql("UPDATE usage_ledger_entry SET quantity = 0").update()))
                .rootCause().hasMessageContaining("permission denied");
        assertThatThrownBy(() -> tenantScope.run(tenant,
                () -> jdbc.sql("DELETE FROM usage_ledger_entry").update()))
                .rootCause().hasMessageContaining("permission denied");
    }

    @Test
    void theCounterAlwaysMatchesTheLedger() {
        ledger.reserve(ctx, aiTokens("a", 2));
        ledger.reserve(ctx, aiTokens("b", 3));
        ledger.reserve(ctx, aiTokens("c", 4));
        ledger.capture(ctx, "a", 1);
        ledger.release(ctx, "b");

        Map<String, Object> fromLedger = tenantScope.call(tenant, () -> jdbc.sql("""
                SELECT COALESCE(SUM(r.quantity) FILTER (WHERE s.id IS NULL), 0) AS open_reserved,
                       COALESCE(SUM(s.quantity) FILTER (WHERE s.kind = 'capture'), 0) AS captured
                FROM usage_ledger_entry r
                LEFT JOIN usage_ledger_entry s
                       ON s.reservation_key = r.reservation_key AND s.kind <> 'reserve'
                WHERE r.kind = 'reserve'
                """).query().singleRow());

        assertThat(tokenBalance()).isEqualTo(new UsageBalance(
                ((Number) fromLedger.get("open_reserved")).longValue(),
                ((Number) fromLedger.get("captured")).longValue()));
        assertThat(tokenBalance()).isEqualTo(new UsageBalance(4, 1));
    }

    private ReservationRequest aiTokens(String key, long quantity) {
        return new ReservationRequest(key, Capabilities.AI_SURVEY_GENERATE, null, quantity, "model-x", TTL);
    }

    private UsageBalance tokenBalance() {
        return ledger.balance(ctx, Meters.AI_TOKENS, null);
    }

    private long entryCount(String kind) {
        return tenantScope.call(tenant, () -> jdbc.sql("SELECT COUNT(*) FROM usage_ledger_entry WHERE kind = :kind")
                .param("kind", kind).query(Long.class).single());
    }
}
