package cn.mjy.platform.entitlement;

/** 判定原因，机器可读；前端与调用方据此决定提示文案与引导（续费、升级、联系管理员）。 */
public enum DecisionReason {
    /** 放行。 */
    ALLOWED,
    /** 能力未登记。 */
    UNKNOWN_CAPABILITY,
    /** 租户没有任何已开始的订阅期。 */
    NO_SUBSCRIPTION,
    /** 当前套餐版本没有开启该能力。 */
    CAPABILITY_NOT_IN_PLAN,
    /** 订阅已到期或已取消，只读：写操作被拒绝。 */
    SUBSCRIPTION_READ_ONLY,
    /** 只读后的导出窗口已过；数据仍保留、仍可查看。 */
    EXPORT_WINDOW_CLOSED,
    /** 当前套餐版本没有为该计量配置额度。 */
    QUOTA_NOT_IN_PLAN,
    /** 剩余额度不足。 */
    QUOTA_EXCEEDED
}
