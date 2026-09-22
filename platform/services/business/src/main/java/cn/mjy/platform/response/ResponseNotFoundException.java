package cn.mjy.platform.response;

/** 问卷不存在或属于别的租户——对调用方两者不可区分，统一 404。 */
public class ResponseNotFoundException extends RuntimeException {

    public ResponseNotFoundException(String message) {
        super(message);
    }
}
