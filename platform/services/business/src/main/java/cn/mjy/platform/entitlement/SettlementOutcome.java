package cn.mjy.platform.entitlement;

/** 结算（capture）或释放（release）的结果，机器可读。 */
public enum SettlementOutcome {
    /** 已结算（含幂等重放）。 */
    CAPTURED,
    /** 已释放（含幂等重放）。 */
    RELEASED,
    /** 本租户下没有这个预占键。 */
    NOT_FOUND,
    /** 已结算，不能再释放。 */
    ALREADY_CAPTURED,
    /** 已释放，不能再结算。 */
    ALREADY_RELEASED,
    /** 已过期回收，不能再结算或释放。 */
    ALREADY_EXPIRED,
    /** 预占已过有效期（尚未回收），不能再结算。 */
    RESERVATION_EXPIRED,
    /** 结算数量超过预占数量。 */
    QUANTITY_EXCEEDS_RESERVATION,
    /** 未经预占的直接结算被额度判定拒绝，原因见 {@link SettlementResult#entitlementReason()}。 */
    NOT_ADMITTED
}
