package cn.mjy.platform.dictionary;

/** 请求本身不成立（形状、上限、树的形状）。映射成 400 invalid_request。 */
public class InvalidDictionaryRequestException extends RuntimeException {

    public InvalidDictionaryRequestException(String message) {
        super(message);
    }
}
