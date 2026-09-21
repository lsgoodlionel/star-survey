package cn.mjy.platform.entitlement;

/** 额度判定结果：放行、拒绝、需要升级（换套餐或加购后可用）。 */
public enum Outcome {
    ALLOW, DENY, NEED_UPGRADE
}
