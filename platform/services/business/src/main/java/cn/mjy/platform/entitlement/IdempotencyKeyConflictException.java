package cn.mjy.platform.entitlement;

/** 同一个幂等键被用于内容不同的请求：属于调用方缺陷，不能当作重试处理。 */
public class IdempotencyKeyConflictException extends RuntimeException {

    public IdempotencyKeyConflictException(String key, String detail) {
        super("idempotency key reused for a different request: " + key + " (" + detail + ")");
    }
}
