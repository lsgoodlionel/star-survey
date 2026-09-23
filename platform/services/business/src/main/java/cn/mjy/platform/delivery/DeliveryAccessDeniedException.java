package cn.mjy.platform.delivery;

import cn.mjy.platform.access.DecisionReason;

/** 403：权限判定不通过，原样带上 access 模块给出的原因。 */
public class DeliveryAccessDeniedException extends RuntimeException {

    private final transient DecisionReason reason;

    public DeliveryAccessDeniedException(DecisionReason reason, String message) {
        super(message);
        this.reason = reason;
    }

    public DecisionReason reason() {
        return reason;
    }
}
