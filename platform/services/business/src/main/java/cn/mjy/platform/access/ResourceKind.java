package cn.mjy.platform.access;

/** 授权可以落在的资源类型：项目 > 文件夹（可嵌套）> 问卷。 */
public enum ResourceKind {
    PROJECT("project"),
    FOLDER("folder"),
    SURVEY("survey");

    private final String code;

    ResourceKind(String code) {
        this.code = code;
    }

    public String code() {
        return code;
    }

    public static ResourceKind fromCode(String code) {
        for (ResourceKind kind : values()) {
            if (kind.code.equals(code)) {
                return kind;
            }
        }
        throw new IllegalArgumentException("unknown resource kind: " + code);
    }

    /** 问卷是叶子，不能再挂子节点。 */
    public boolean canContainChildren() {
        return this != SURVEY;
    }
}
