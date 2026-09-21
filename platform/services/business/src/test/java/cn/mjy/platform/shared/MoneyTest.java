package cn.mjy.platform.shared;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import org.junit.jupiter.api.Test;

/** 金额一律用最小币种单位的整数，币种必填，不同币种不能相加。 */
class MoneyTest {

    @Test
    void addsAmountsOfTheSameCurrency() {
        Money total = Money.of(1250, "CNY").add(Money.of(50, "CNY"));

        assertThat(total).isEqualTo(Money.of(1300, "CNY"));
    }

    @Test
    void refusesToAddDifferentCurrencies() {
        assertThatThrownBy(() -> Money.of(100, "CNY").add(Money.of(100, "USD")))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("CNY")
                .hasMessageContaining("USD");
    }

    @Test
    void requiresAnIsoCurrencyCode() {
        assertThatThrownBy(() -> Money.of(100, null)).isInstanceOf(IllegalArgumentException.class);
        assertThatThrownBy(() -> Money.of(100, "yuan")).isInstanceOf(IllegalArgumentException.class);
    }

    @Test
    void negativeAmountsAreAllowedForRefundsAndReversals() {
        assertThat(Money.of(-300, "CNY").amountMinor()).isEqualTo(-300);
    }

    @Test
    void additionOverflowIsAnErrorNotASilentWrapAround() {
        assertThatThrownBy(() -> Money.of(Long.MAX_VALUE, "CNY").add(Money.of(1, "CNY")))
                .isInstanceOf(ArithmeticException.class);
    }
}
