package cn.mjy.platform.access;

import java.util.Arrays;
import java.util.Optional;

/**
 * 可判定的动作（权限码）。代码与 access_permission 表一一对应，由测试保证两边一致；
 * 哪个角色拥有哪些权限只由 access_role_permission 表决定。
 */
public enum Permission {
    VIEW("view"),
    EDIT("edit"),
    PUBLISH("publish"),
    PUBLISH_WITHOUT_APPROVAL("publish-without-approval"),
    APPROVE_PUBLISH("approve-publish"),
    VIEW_STATISTICS("view-statistics"),
    VIEW_RAW_RESPONSES("view-raw-responses"),
    EXPORT_RAW_RESPONSES("export-raw-responses"),
    VIEW_SENSITIVE_FIELDS("view-sensitive-fields"),
    MANAGE_MEMBERS("manage-members"),
    MANAGE_SETTINGS("manage-settings"),
    VIEW_BILLING("view-billing"),
    MANAGE_BILLING("manage-billing"),
    GRADE("grade"),
    INTERVIEW("interview"),
    RESPOND("respond");

    private final String code;

    Permission(String code) {
        this.code = code;
    }

    public String code() {
        return code;
    }

    public static Optional<Permission> fromCode(String code) {
        return Arrays.stream(values()).filter(p -> p.code.equals(code)).findFirst();
    }
}
