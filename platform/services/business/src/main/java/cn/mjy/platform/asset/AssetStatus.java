package cn.mjy.platform.asset;

import java.util.Arrays;
import java.util.Optional;

/** 资产状态。{@link #ARCHIVED} 是「停用」：不再能被新的发布引用，已发布的问卷照常回放。 */
public enum AssetStatus {
    ACTIVE("active"),
    ARCHIVED("archived");

    private final String code;

    AssetStatus(String code) {
        this.code = code;
    }

    public String code() {
        return code;
    }

    public static Optional<AssetStatus> fromCode(String code) {
        return Arrays.stream(values()).filter(status -> status.code.equals(code)).findFirst();
    }
}
