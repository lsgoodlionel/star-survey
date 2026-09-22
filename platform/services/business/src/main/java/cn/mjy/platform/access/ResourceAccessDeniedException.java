package cn.mjy.platform.access;

/** 权限判定拒绝；reason 原样来自 {@link AccessDecisionService}。映射为 403。 */
public class ResourceAccessDeniedException extends RuntimeException {

    private final DecisionReason reason;

    public ResourceAccessDeniedException(DecisionReason reason, String message) {
        super(message);
        this.reason = reason;
    }

    public DecisionReason reason() {
        return reason;
    }
}
