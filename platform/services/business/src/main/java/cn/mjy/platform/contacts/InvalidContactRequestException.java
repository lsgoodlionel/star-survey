package cn.mjy.platform.contacts;

/** 请求本身不合法（字段缺失、格式不对、类别与名单不符），对外 400。 */
public class InvalidContactRequestException extends RuntimeException {

    public InvalidContactRequestException(String message) {
        super(message);
    }
}
