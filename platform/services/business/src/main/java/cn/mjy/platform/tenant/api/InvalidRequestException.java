package cn.mjy.platform.tenant.api;

/** 请求内容不合法（未知的状态值等），对应 400。 */
public class InvalidRequestException extends RuntimeException {

    public InvalidRequestException(String message) {
        super(message);
    }
}
