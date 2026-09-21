package cn.mjy.platform.entitlement;

/** 能力的访问性质：决定订阅到期或取消（只读）后是否还能使用。 */
public enum AccessKind {
    READ, EXPORT, WRITE
}
