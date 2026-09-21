package cn.mjy.platform.access;

import java.util.Set;

/** 角色目录中的一行：角色码、约束与权限集合（来自数据库，不在代码里写死）。 */
public record RoleDefinition(String code, boolean tenantWideOnly, Integer maxGrantHours, boolean requiresSeat,
        Set<Permission> permissions) {

    public RoleDefinition {
        permissions = Set.copyOf(permissions);
    }
}
