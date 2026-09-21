package cn.mjy.platform.shared;

import java.util.regex.Pattern;

/**
 * 金额：最小币种单位的整数（分），币种必填。允许负数，用于退款与冲正。
 * 不提供浮点构造，杜绝舍入误差进入账本。
 */
public record Money(long amountMinor, String currency) {

    private static final Pattern ISO_CURRENCY = Pattern.compile("[A-Z]{3}");

    public Money {
        if (currency == null || !ISO_CURRENCY.matcher(currency).matches()) {
            throw new IllegalArgumentException("currency must be an ISO 4217 code, got: " + currency);
        }
    }

    public static Money of(long amountMinor, String currency) {
        return new Money(amountMinor, currency);
    }

    public Money add(Money other) {
        if (!currency.equals(other.currency)) {
            throw new IllegalArgumentException("cannot add " + currency + " and " + other.currency);
        }
        return new Money(Math.addExact(amountMinor, other.amountMinor), currency);
    }
}
