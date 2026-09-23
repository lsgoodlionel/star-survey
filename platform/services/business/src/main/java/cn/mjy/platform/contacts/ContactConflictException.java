package cn.mjy.platform.contacts;

/** 与现有数据冲突（令牌已被占用、部门成环等），对外 409 并带稳定错误码。 */
public class ContactConflictException extends RuntimeException {

    private final String code;

    public ContactConflictException(String code, String message) {
        super(message);
        this.code = code;
    }

    public String code() {
        return code;
    }
}
