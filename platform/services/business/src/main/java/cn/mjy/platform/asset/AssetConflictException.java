package cn.mjy.platform.asset;

/** 与现有状态冲突（被问卷引用着的资产不能删除等），对外 409 并带稳定错误码。 */
public class AssetConflictException extends RuntimeException {

    /** 还被某一版问卷引用着：只能停用，不能删除（ADR 0019 决定 6）。 */
    public static final String ASSET_IN_USE = "asset_in_use";

    /** 已停用的资产不能被新的发布引用。 */
    public static final String ASSET_ARCHIVED = "asset_archived";

    /**
     * 入库时这个 id 已经被别的东西占着（ADR 0019 决定 8）。
     * 挡的是「引擎侧编一个 upload_token 去顶掉一件作者素材」。
     */
    public static final String ASSET_ID_TAKEN = "asset_id_taken";

    /** 作答者上传的资产不是素材：不许追加版本，也不许从素材库删。 */
    public static final String RESPONDENT_ASSET = "respondent_asset";

    /** 匿名作答没有参与者令牌，签不出绑定票——失败即关闭，不签一张谁都能用的。 */
    public static final String NO_RESPONDENT_BINDING = "no_respondent_binding";

    private final String code;

    public AssetConflictException(String code, String message) {
        super(message);
        this.code = code;
    }

    public String code() {
        return code;
    }
}
