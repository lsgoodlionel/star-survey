package cn.mjy.platform.entitlement;

/**
 * 订阅在某一时刻的状态。EXPIRED 与 CANCELLED 都是只读：可读、导出窗口内可导出，写一律拒绝；
 * 数据从不自动删除（P-04）。
 */
public enum SubscriptionStatus {
    TRIAL, ACTIVE, EXPIRED, CANCELLED;

    public boolean isReadOnly() {
        return this == EXPIRED || this == CANCELLED;
    }
}
