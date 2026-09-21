package cn.mjy.platform.entitlement;

import static org.assertj.core.api.Assertions.assertThat;

import cn.mjy.platform.shared.TenantContext;
import cn.mjy.platform.shared.TenantId;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;

/**
 * 计费只计有效完成答卷（P-03）：只有 captureValidCompleted 会计入；放入名额但未完成的答卷释放后不计；
 * 引擎事件重投不重复计费；每份问卷的采集档位（P-02）各自独立。
 */
@SpringBootTest
class ValidResponseMeterTest {

    private static final String TRACE = "trace-engine";

    @Autowired
    private ValidResponseMeter meter;

    @Autowired
    private UsageLedger ledger;

    @Autowired
    private EntitlementFixtures fixtures;

    private TenantId tenant;
    private TenantContext ctx;

    @BeforeEach
    void tenantWithThreeResponsesPerSurvey() {
        tenant = fixtures.activeTenantWithQuota(3);
        ctx = EntitlementFixtures.contextOf(tenant);
    }

    @Test
    void anAdmittedResponseCountsOnlyOnceItIsCapturedAsValidAndCompleted() {
        assertThat(meter.admit(tenant, "survey-1", "resp-1", TRACE).isGranted()).isTrue();
        assertThat(billed("survey-1")).isEqualTo(new UsageBalance(1, 0));

        SettlementResult captured = meter.captureValidCompleted(tenant, "survey-1", "resp-1", TRACE);

        assertThat(captured.outcome()).isEqualTo(SettlementOutcome.CAPTURED);
        assertThat(billed("survey-1")).isEqualTo(new UsageBalance(0, 1));
    }

    @Test
    void redeliveredCompletionEventsNeverBillTwice() {
        meter.admit(tenant, "survey-1", "resp-1", TRACE);
        meter.captureValidCompleted(tenant, "survey-1", "resp-1", TRACE);

        SettlementResult redelivered = meter.captureValidCompleted(tenant, "survey-1", "resp-1", TRACE);

        assertThat(redelivered.replayed()).isTrue();
        assertThat(billed("survey-1")).isEqualTo(new UsageBalance(0, 1));
    }

    @Test
    void aResponseThatNeverCompletesIsReleasedAndNotBilled() {
        meter.admit(tenant, "survey-1", "resp-1", TRACE);

        SettlementResult released = meter.releaseUncompleted(tenant, "survey-1", "resp-1", TRACE);

        assertThat(released.outcome()).isEqualTo(SettlementOutcome.RELEASED);
        assertThat(billed("survey-1")).isEqualTo(UsageBalance.EMPTY);
    }

    @Test
    void aCompletionWithoutPriorAdmissionIsStillCountedWhenQuotaAllows() {
        SettlementResult captured = meter.captureValidCompleted(tenant, "survey-1", "resp-9", TRACE);

        assertThat(captured.outcome()).isEqualTo(SettlementOutcome.CAPTURED);
        assertThat(billed("survey-1")).isEqualTo(new UsageBalance(0, 1));
        assertThat(meter.captureValidCompleted(tenant, "survey-1", "resp-9", TRACE).replayed()).isTrue();
        assertThat(billed("survey-1")).isEqualTo(new UsageBalance(0, 1));
    }

    @Test
    void theCollectionTierIsEnforcedPerSurvey() {
        for (int i = 1; i <= 3; i++) {
            assertThat(meter.admit(tenant, "survey-1", "resp-" + i, TRACE).isGranted()).isTrue();
        }

        ReservationResult fourth = meter.admit(tenant, "survey-1", "resp-4", TRACE);

        assertThat(fourth.decision()).isEqualTo(Decision.needUpgrade(DecisionReason.QUOTA_EXCEEDED));
        assertThat(meter.admit(tenant, "survey-2", "resp-1", TRACE).isGranted()).isTrue();
    }

    @Test
    void aCompletionBeyondTheTierIsReportedInsteadOfSilentlyBilled() {
        for (int i = 1; i <= 3; i++) {
            meter.captureValidCompleted(tenant, "survey-1", "resp-" + i, TRACE);
        }

        SettlementResult overflow = meter.captureValidCompleted(tenant, "survey-1", "resp-4", TRACE);

        assertThat(overflow.outcome()).isEqualTo(SettlementOutcome.NOT_ADMITTED);
        assertThat(overflow.entitlementReason()).isEqualTo(DecisionReason.QUOTA_EXCEEDED);
        assertThat(billed("survey-1")).isEqualTo(new UsageBalance(0, 3));
    }

    private UsageBalance billed(String surveyId) {
        return ledger.balance(ctx, Meters.VALID_COMPLETED_RESPONSE, surveyId);
    }
}
