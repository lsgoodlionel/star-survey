package cn.mjy.platform.entitlement;

/** 某计量在某对象、某周期上的余额：预占中与已结算。 */
public record UsageBalance(long reserved, long captured) {

    public static final UsageBalance EMPTY = new UsageBalance(0, 0);

    public long used() {
        return reserved + captured;
    }
}
