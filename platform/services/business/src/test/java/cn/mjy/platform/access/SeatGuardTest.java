package cn.mjy.platform.access;

import static org.assertj.core.api.Assertions.assertThatCode;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import cn.mjy.platform.shared.SeatAllowance;
import cn.mjy.platform.shared.TenantId;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.support.StaticListableBeanFactory;

/** 席位判定的纯逻辑：额度实现缺失时失败即关闭，而不是放行。 */
class SeatGuardTest {

    private final TenantId tenant = TenantId.random();

    @Test
    void withoutASeatAllowanceNoSeatIsAvailable() {
        SeatGuard guard = new SeatGuard(new StaticListableBeanFactory().getBeanProvider(SeatAllowance.class));

        assertThatThrownBy(() -> guard.requireFreeSeat(tenant, 0)).isInstanceOf(SeatLimitExceededException.class);
    }

    @Test
    void aSeatIsAvailableOnlyBelowTheLimit() {
        StaticListableBeanFactory beans = new StaticListableBeanFactory();
        beans.addBean("seats", (SeatAllowance) t -> 3);
        SeatGuard guard = new SeatGuard(beans.getBeanProvider(SeatAllowance.class));

        assertThatCode(() -> guard.requireFreeSeat(tenant, 2)).doesNotThrowAnyException();
        assertThatThrownBy(() -> guard.requireFreeSeat(tenant, 3)).isInstanceOf(SeatLimitExceededException.class)
                .hasMessageContaining("3");
    }

    @Test
    void aNegativeLimitFromTheAllowanceIsTreatedAsZero() {
        StaticListableBeanFactory beans = new StaticListableBeanFactory();
        beans.addBean("seats", (SeatAllowance) t -> -5);
        SeatGuard guard = new SeatGuard(beans.getBeanProvider(SeatAllowance.class));

        assertThatThrownBy(() -> guard.requireFreeSeat(tenant, 0)).isInstanceOf(SeatLimitExceededException.class);
    }
}
