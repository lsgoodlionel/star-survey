package cn.mjy.platform.entitlement;

import java.util.Objects;

/**
 * 结算或释放的结果。{@code replayed} 为 true 表示同一操作此前已生效，这次没有再改余额。
 * {@code entitlementReason} 仅在 {@link SettlementOutcome#NOT_ADMITTED} 时非空。
 */
public record SettlementResult(SettlementOutcome outcome, long quantity, boolean replayed,
                               DecisionReason entitlementReason) {

    public SettlementResult {
        Objects.requireNonNull(outcome, "outcome");
    }

    static SettlementResult applied(SettlementOutcome outcome, long quantity, boolean replayed) {
        return new SettlementResult(outcome, quantity, replayed, null);
    }

    static SettlementResult rejected(SettlementOutcome outcome) {
        return new SettlementResult(outcome, 0, false, null);
    }

    static SettlementResult notAdmitted(DecisionReason reason) {
        return new SettlementResult(SettlementOutcome.NOT_ADMITTED, 0, false, reason);
    }

    /** 这次调用之后，该预占处于本次请求期望的终态（首次生效或幂等重放）。 */
    public boolean isApplied() {
        return outcome == SettlementOutcome.CAPTURED || outcome == SettlementOutcome.RELEASED;
    }
}
