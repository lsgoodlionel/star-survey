package cn.mjy.platform.entitlement;

/** 用量何时归零：每个订阅期重新计，或永不归零。 */
public enum ResetPolicy {
    SUBSCRIPTION_PERIOD, NEVER
}
