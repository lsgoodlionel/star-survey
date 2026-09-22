package cn.mjy.platform.access;

/** 与树的当前状态冲突（如移到自己的子孙下）。映射为 409，code 是机器可读的错误码。 */
public class ResourceConflictException extends RuntimeException {

    public static final String MOVE_INTO_DESCENDANT = "move_into_descendant";
    public static final String TREE_BUSY = "tree_busy";

    private final String code;

    public ResourceConflictException(String code, String message) {
        super(message);
        this.code = code;
    }

    public String code() {
        return code;
    }
}
