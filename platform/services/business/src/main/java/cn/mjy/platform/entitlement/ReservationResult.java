package cn.mjy.platform.entitlement;

import java.time.Instant;
import java.util.Objects;

/**
 * 预占结果。放行时 {@code decision} 为 ALLOW；被拒时带判定原因，且没有占用任何额度。
 * {@code replayed} 为 true 表示同一个幂等键此前已预占成功，这次只是返回原结果，没有再占额度。
 */
public record ReservationResult(Decision decision, String idempotencyKey, long quantity, Instant expiresAt,
                                boolean replayed) {

    public ReservationResult {
        Objects.requireNonNull(decision, "decision");
        Objects.requireNonNull(idempotencyKey, "idempotencyKey");
    }

    static ReservationResult granted(String key, long quantity, Instant expiresAt, boolean replayed) {
        return new ReservationResult(Decision.allow(), key, quantity, expiresAt, replayed);
    }

    static ReservationResult rejected(String key, Decision decision) {
        return new ReservationResult(decision, key, 0, null, false);
    }

    public boolean isGranted() {
        return decision.isAllowed();
    }
}
