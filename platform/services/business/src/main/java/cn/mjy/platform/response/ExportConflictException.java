package cn.mjy.platform.response;

import java.util.Objects;

/** 与作业当前状态冲突（409），带机器可读的错误码，如 {@code idempotency_key_reused}。 */
public class ExportConflictException extends RuntimeException {

    private final String code;

    public ExportConflictException(String code, String message) {
        super(message);
        this.code = Objects.requireNonNull(code, "code");
    }

    public String code() {
        return code;
    }
}
