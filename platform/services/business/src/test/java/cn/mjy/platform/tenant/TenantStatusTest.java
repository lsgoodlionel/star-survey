package cn.mjy.platform.tenant;

import static cn.mjy.platform.tenant.TenantStatus.ACTIVE;
import static cn.mjy.platform.tenant.TenantStatus.CLOSED;
import static cn.mjy.platform.tenant.TenantStatus.PROVISIONING;
import static cn.mjy.platform.tenant.TenantStatus.SUSPENDED;
import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import cn.mjy.platform.tenant.api.IllegalTransitionException;
import java.util.EnumSet;
import java.util.Set;
import java.util.stream.Stream;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.Arguments;
import org.junit.jupiter.params.provider.EnumSource;
import org.junit.jupiter.params.provider.MethodSource;

/** 租户状态机：开通中 -> 启用 -> 暂停 -> 启用；任意非终态 -> 关闭；关闭是终态。 */
class TenantStatusTest {

    private static final Set<String> LEGAL = Set.of(
            "PROVISIONING->ACTIVE", "PROVISIONING->CLOSED",
            "ACTIVE->SUSPENDED", "ACTIVE->CLOSED",
            "SUSPENDED->ACTIVE", "SUSPENDED->CLOSED");

    static Stream<Arguments> legalTransitions() {
        return allPairs().filter(a -> LEGAL.contains(key(a)));
    }

    static Stream<Arguments> illegalTransitions() {
        return allPairs().filter(a -> !LEGAL.contains(key(a)));
    }

    private static Stream<Arguments> allPairs() {
        return EnumSet.allOf(TenantStatus.class).stream()
                .flatMap(from -> EnumSet.allOf(TenantStatus.class).stream().map(to -> Arguments.of(from, to)));
    }

    private static String key(Arguments a) {
        return a.get()[0] + "->" + a.get()[1];
    }

    @ParameterizedTest(name = "{0} -> {1} 合法")
    @MethodSource("legalTransitions")
    void everyLegalTransitionIsAllowed(TenantStatus from, TenantStatus to) {
        assertThat(from.canTransitionTo(to)).isTrue();
        assertThat(from.transitionTo(to)).isEqualTo(to);
    }

    @ParameterizedTest(name = "{0} -> {1} 非法")
    @MethodSource("illegalTransitions")
    void everyIllegalTransitionIsRejectedWithAClearError(TenantStatus from, TenantStatus to) {
        assertThat(from.canTransitionTo(to)).isFalse();
        assertThatThrownBy(() -> from.transitionTo(to))
                .isInstanceOf(IllegalTransitionException.class)
                .hasMessageContaining(from.code())
                .hasMessageContaining(to.code());
    }

    @ParameterizedTest
    @EnumSource(TenantStatus.class)
    void closedIsTerminal(TenantStatus target) {
        assertThat(CLOSED.canTransitionTo(target)).isFalse();
    }

    @Test
    void theFullLifecycleIsReachable() {
        TenantStatus status = PROVISIONING
                .transitionTo(ACTIVE)
                .transitionTo(SUSPENDED)
                .transitionTo(ACTIVE)
                .transitionTo(CLOSED);

        assertThat(status).isEqualTo(CLOSED);
    }

    @Test
    void statusCodesRoundTripAndUnknownCodesAreRejected() {
        for (TenantStatus status : TenantStatus.values()) {
            assertThat(TenantStatus.fromCode(status.code())).isEqualTo(status);
        }
        assertThat(ACTIVE.code()).isEqualTo("active");
        assertThatThrownBy(() -> TenantStatus.fromCode("deleted")).isInstanceOf(IllegalArgumentException.class);
        assertThatThrownBy(() -> TenantStatus.fromCode(null)).isInstanceOf(IllegalArgumentException.class);
    }
}
