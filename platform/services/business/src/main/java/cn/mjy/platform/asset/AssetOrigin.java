package cn.mjy.platform.asset;

import java.util.Arrays;
import java.util.Optional;

/**
 * 资产是谁放进来的（ADR 0019 决定 8）。这不是一个标签，是一组规则的开关：
 * {@link #RESPONDENT} 的资产不进素材库列表、不许追加版本、不许从库里删、
 * <b>不许被问卷定义引用</b>，取件走绑定作答者的票（决定 9）。
 */
public enum AssetOrigin {

    /** 已认证的作者从素材库上传。 */
    AUTHOR("author"),

    /** 作答者在作答时上传，由上传会话驱动入库；它是答卷数据，不是素材。 */
    RESPONDENT("respondent");

    private final String code;

    AssetOrigin(String code) {
        this.code = code;
    }

    public String code() {
        return code;
    }

    public static Optional<AssetOrigin> fromCode(String code) {
        return Arrays.stream(values()).filter(origin -> origin.code.equals(code)).findFirst();
    }
}
