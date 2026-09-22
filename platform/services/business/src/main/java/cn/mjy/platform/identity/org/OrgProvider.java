package cn.mjy.platform.identity.org;

import java.util.Arrays;
import java.util.Optional;

/** 支持组织免登的开放平台。代码值同时是 identity_binding.provider 的取值。 */
public enum OrgProvider {
    WECOM("wecom"),
    DINGTALK("dingtalk"),
    FEISHU("feishu");

    public static final String CODE_REGEX = "wecom|dingtalk|feishu";

    private final String code;

    OrgProvider(String code) {
        this.code = code;
    }

    public String code() {
        return code;
    }

    public static Optional<OrgProvider> fromCode(String code) {
        return Arrays.stream(values()).filter(p -> p.code.equals(code)).findFirst();
    }
}
