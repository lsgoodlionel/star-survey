package cn.mjy.platform.tenant.api;

/** 请求与资源当前状态冲突（重复、并发改动、对象状态不允许），对应 409。 */
public class ConflictException extends RuntimeException {

    public ConflictException(String message) {
        super(message);
    }
}
