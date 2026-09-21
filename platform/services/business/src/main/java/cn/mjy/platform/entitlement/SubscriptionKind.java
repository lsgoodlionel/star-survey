package cn.mjy.platform.entitlement;

/** 订阅期的种类（落库值）。到期不是种类，而是由时间推导出的状态，见 {@link SubscriptionStatus}。 */
public enum SubscriptionKind {
    TRIAL, PAID, CANCELLED
}
