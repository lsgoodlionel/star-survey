package cn.mjy.platform.response;

/** 查询参数不合法（状态、游标、条数）→ 400。 */
public class InvalidResponseQueryException extends RuntimeException {

    public InvalidResponseQueryException(String message) {
        super(message);
    }
}
