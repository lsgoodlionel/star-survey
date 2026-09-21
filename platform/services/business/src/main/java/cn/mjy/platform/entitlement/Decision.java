package cn.mjy.platform.entitlement;

import java.util.Objects;

/** 一次额度判定：结果加机器可读原因。 */
public record Decision(Outcome outcome, DecisionReason reason) {

    private static final Decision ALLOW = new Decision(Outcome.ALLOW, DecisionReason.ALLOWED);

    public Decision {
        Objects.requireNonNull(outcome, "outcome");
        Objects.requireNonNull(reason, "reason");
        if ((outcome == Outcome.ALLOW) != (reason == DecisionReason.ALLOWED)) {
            throw new IllegalArgumentException(outcome + " cannot carry reason " + reason);
        }
    }

    public static Decision allow() {
        return ALLOW;
    }

    public static Decision deny(DecisionReason reason) {
        return new Decision(Outcome.DENY, reason);
    }

    public static Decision needUpgrade(DecisionReason reason) {
        return new Decision(Outcome.NEED_UPGRADE, reason);
    }

    public boolean isAllowed() {
        return outcome == Outcome.ALLOW;
    }
}
