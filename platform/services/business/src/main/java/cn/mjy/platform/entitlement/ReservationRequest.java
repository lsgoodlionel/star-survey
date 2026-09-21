package cn.mjy.platform.entitlement;

import java.time.Duration;
import java.util.Objects;

/**
 * 一次预占请求。
 *
 * @param idempotencyKey 幂等业务键（同一租户内唯一），重试必须带同一个键
 * @param capability     要使用的能力；决定消耗哪个计量
 * @param subject        按问卷累计的计量填问卷标识，按租户累计的填 null
 * @param quantity       预占数量，必须为正
 * @param model          AI 模型标识（可选），与能力、计量一起入账，供版本化价目表定价（U-02）
 * @param ttl            预占有效期；过期未结算的预占可被回收
 */
public record ReservationRequest(String idempotencyKey, String capability, String subject, long quantity,
                                 String model, Duration ttl) {

    static final int MAX_KEY_LENGTH = 200;
    private static final int MAX_MODEL_LENGTH = 100;
    private static final Duration MAX_TTL = Duration.ofDays(30);

    public ReservationRequest {
        requireKey(idempotencyKey);
        Objects.requireNonNull(capability, "capability");
        Objects.requireNonNull(ttl, "ttl");
        if (quantity <= 0) {
            throw new IllegalArgumentException("quantity must be positive, got " + quantity);
        }
        if (ttl.isNegative() || ttl.isZero() || ttl.compareTo(MAX_TTL) > 0) {
            throw new IllegalArgumentException("ttl must be positive and at most " + MAX_TTL);
        }
        if (model != null && (model.isBlank() || model.length() > MAX_MODEL_LENGTH)) {
            throw new IllegalArgumentException("model must be non-blank and at most " + MAX_MODEL_LENGTH + " chars");
        }
    }

    static String requireKey(String key) {
        if (key == null || key.isBlank() || key.length() > MAX_KEY_LENGTH) {
            throw new IllegalArgumentException("idempotency key must be non-blank and at most "
                    + MAX_KEY_LENGTH + " chars");
        }
        return key;
    }
}
