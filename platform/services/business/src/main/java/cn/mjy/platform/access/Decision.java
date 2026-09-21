package cn.mjy.platform.access;

import java.util.Objects;

/** 一次权限判定的结果：是否允许、原因，以及便于排查的说明（命中的角色与范围）。 */
public record Decision(boolean allowed, DecisionReason reason, String detail) {

    public Decision {
        Objects.requireNonNull(reason, "reason");
        Objects.requireNonNull(detail, "detail");
        if (allowed != (reason == DecisionReason.GRANTED)) {
            throw new IllegalArgumentException("allowed must match reason GRANTED");
        }
    }

    static Decision allow(String detail) {
        return new Decision(true, DecisionReason.GRANTED, detail);
    }

    static Decision deny(DecisionReason reason, String detail) {
        return new Decision(false, reason, detail);
    }
}
