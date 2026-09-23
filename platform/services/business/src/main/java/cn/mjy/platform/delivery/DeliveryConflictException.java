package cn.mjy.platform.delivery;

/** 409：与当前状态冲突（任务已结束、规则已存在、幂等键复用于不同请求体等）。 */
public class DeliveryConflictException extends RuntimeException {

    private final String code;

    public DeliveryConflictException(String code, String message) {
        super(message);
        this.code = code;
    }

    public String code() {
        return code;
    }
}
