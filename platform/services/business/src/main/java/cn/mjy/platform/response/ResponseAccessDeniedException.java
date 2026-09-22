package cn.mjy.platform.response;

import cn.mjy.platform.access.DecisionReason;
import java.util.Objects;

/** 权限判定不通过（403），携带 access 模块给出的原因。 */
public class ResponseAccessDeniedException extends RuntimeException {

    private final DecisionReason reason;

    public ResponseAccessDeniedException(DecisionReason reason, String message) {
        super(message);
        this.reason = Objects.requireNonNull(reason, "reason");
    }

    public DecisionReason reason() {
        return reason;
    }
}
