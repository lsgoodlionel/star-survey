package cn.mjy.platform.identity.org;

import java.util.Arrays;
import java.util.Optional;

/**
 * 组织里平台尚不认识的人来免登时怎么办（ADR 0014）。
 *
 * <ul>
 *   <li>{@link #REQUIRE_PREAUTHORIZED}（默认）：必须先由管理员预授权（绑定身份并按员工邀请）才能登录；</li>
 *   <li>{@link #JIT_STAFF}：首次登录即开通为员工并占一个席位，不授予任何角色。</li>
 * </ul>
 */
public enum UnknownUserPolicy {
    REQUIRE_PREAUTHORIZED("require_preauthorized"),
    JIT_STAFF("jit_staff");

    public static final String CODE_REGEX = "require_preauthorized|jit_staff";

    private final String code;

    UnknownUserPolicy(String code) {
        this.code = code;
    }

    public String code() {
        return code;
    }

    public static Optional<UnknownUserPolicy> fromCode(String code) {
        return Arrays.stream(values()).filter(p -> p.code.equals(code)).findFirst();
    }
}
