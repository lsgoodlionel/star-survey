package cn.mjy.platform.tenant.api;

import java.util.function.Function;

/** 请求内容不合法（未知的状态值等），对应 400。 */
public class InvalidRequestException extends RuntimeException {

    public InvalidRequestException(String message) {
        super(message);
    }

    /** 解析客户端传来的值：解析器抛出的 IllegalArgumentException 转为 400。 */
    public static <T> T parse(String value, Function<String, T> parser) {
        try {
            return parser.apply(value);
        } catch (IllegalArgumentException e) {
            throw new InvalidRequestException(e.getMessage());
        }
    }
}
