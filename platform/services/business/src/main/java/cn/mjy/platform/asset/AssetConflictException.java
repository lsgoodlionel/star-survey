package cn.mjy.platform.asset;

/** 与现有状态冲突（被问卷引用着的资产不能删除等），对外 409 并带稳定错误码。 */
public class AssetConflictException extends RuntimeException {

    /** 还被某一版问卷引用着：只能停用，不能删除（ADR 0019 决定 6）。 */
    public static final String ASSET_IN_USE = "asset_in_use";

    /** 已停用的资产不能被新的发布引用。 */
    public static final String ASSET_ARCHIVED = "asset_archived";

    private final String code;

    public AssetConflictException(String code, String message) {
        super(message);
        this.code = code;
    }

    public String code() {
        return code;
    }
}
