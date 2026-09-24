package cn.mjy.platform.asset;

import java.util.Arrays;
import java.util.Optional;

/** 资产类别。由内容嗅探结果推出，不由调用方声明（ADR 0019 决定 3）。 */
public enum AssetKind {
    IMAGE("image"),
    AUDIO("audio"),
    VIDEO("video");

    private final String code;

    AssetKind(String code) {
        this.code = code;
    }

    public String code() {
        return code;
    }

    public static Optional<AssetKind> fromCode(String code) {
        return Arrays.stream(values()).filter(kind -> kind.code.equals(code)).findFirst();
    }
}
