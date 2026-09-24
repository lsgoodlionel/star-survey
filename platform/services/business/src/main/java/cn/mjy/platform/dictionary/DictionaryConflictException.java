package cn.mjy.platform.dictionary;

/** 状态不允许这个动作（重名、改已发布的版本）。映射成 409 与一个稳定的错误码。 */
public class DictionaryConflictException extends RuntimeException {

    private final String code;

    public DictionaryConflictException(String code, String message) {
        super(message);
        this.code = code;
    }

    public String code() {
        return code;
    }
}
