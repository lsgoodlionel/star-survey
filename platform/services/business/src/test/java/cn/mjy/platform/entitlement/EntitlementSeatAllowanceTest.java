package cn.mjy.platform.entitlement;

import static org.assertj.core.api.Assertions.assertThat;

import cn.mjy.platform.shared.TenantId;
import java.time.Duration;
import java.time.Instant;
import java.util.Map;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;

/**
 * 席位上限来自当前订阅的套餐（计量 member.seats）。权限模块在每次邀请时询问；
 * 任何无法给出明确额度的情况都返回 0（失败即关闭：宁可拒绝邀请，也不放开席位）。
 */
@SpringBootTest
class EntitlementSeatAllowanceTest {

    @Autowired
    private EntitlementSeatAllowance seats;

    @Autowired
    private EntitlementFixtures fixtures;

    @Autowired
    private SubscriptionService subscriptions;

    @Test
    void theLimitIsThePlansSeatQuota() {
        TenantId tenant = fixtures.activeTenant(EntitlementFixtures.ALL_CAPABILITIES, Map.of(Meters.MEMBER_SEATS, 5L));

        assertThat(seats.seatLimit(tenant)).isEqualTo(5);
    }

    @Test
    void aPlanWithoutASeatQuotaGivesNoSeats() {
        TenantId tenant = fixtures.activeTenant(EntitlementFixtures.ALL_CAPABILITIES, Map.of());

        assertThat(seats.seatLimit(tenant)).isZero();
    }

    @Test
    void aTenantWithoutASubscriptionGivesNoSeats() {
        assertThat(seats.seatLimit(TenantId.random())).isZero();
    }

    @Test
    void aReadOnlySubscriptionCannotAddMembers() {
        TenantId tenant = TenantId.random();
        PlanVersion plan = fixtures.publish(EntitlementFixtures.ALL_CAPABILITIES, Map.of(Meters.MEMBER_SEATS, 5L));
        Instant now = Instant.now();
        subscriptions.start(tenant, plan.id(), SubscriptionKind.PAID,
                now.minus(Duration.ofDays(60)), now.minus(Duration.ofDays(1)));

        assertThat(seats.seatLimit(tenant)).isZero();
    }

    @Test
    void aTrialSubscriptionGetsItsPlansSeats() {
        TenantId tenant = TenantId.random();
        PlanVersion plan = fixtures.publish(EntitlementFixtures.ALL_CAPABILITIES, Map.of(Meters.MEMBER_SEATS, 3L));
        Instant now = Instant.now();
        subscriptions.start(tenant, plan.id(), SubscriptionKind.TRIAL,
                now.minus(Duration.ofDays(1)), now.plus(Duration.ofDays(14)));

        assertThat(seats.seatLimit(tenant)).isEqualTo(3);
    }

    @Test
    void aQuotaBeyondIntRangeIsCappedRatherThanOverflowing() {
        TenantId tenant = fixtures.activeTenant(EntitlementFixtures.ALL_CAPABILITIES,
                Map.of(Meters.MEMBER_SEATS, 10_000_000_000L));

        assertThat(seats.seatLimit(tenant)).isEqualTo(Integer.MAX_VALUE);
    }
}
