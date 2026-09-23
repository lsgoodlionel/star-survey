package cn.mjy.platform.contacts;

import java.util.Locale;
import java.util.Optional;
import com.fasterxml.jackson.annotation.JsonValue;

/**
 * 三类业务身份（ADR 0017 决定 1）。同一个自然人可以同时是三者，平台永不隐式合并：
 * 每一类各有自己的行、自己的生命周期与权限，只能通过 {@code contact_link} 显式关联。
 */
public enum ContactKind {

    /** 平台员工账号：与 access 模块的成员（actor_id）对应。 */
    STAFF,
    /** 组织成员：来自组织免登 / 通讯录同步，与 identity_binding 的主体对应。 */
    ORG_MEMBER,
    /** 答卷联系人：导入、API 沉淀或答卷授权而来的受访者。 */
    RESPONDENT;

    @JsonValue
    public String code() {
        return name().toLowerCase(Locale.ROOT);
    }

    public static Optional<ContactKind> fromCode(String code) {
        if (code == null) {
            return Optional.empty();
        }
        for (ContactKind kind : values()) {
            if (kind.code().equals(code)) {
                return Optional.of(kind);
            }
        }
        return Optional.empty();
    }

    static ContactKind fromDb(String code) {
        return fromCode(code).orElseThrow(() -> new IllegalStateException("unknown contact kind: " + code));
    }
}
