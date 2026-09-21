package cn.mjy.platform.access;

import java.util.HashMap;
import java.util.HashSet;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import java.util.Set;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.stereotype.Repository;

/**
 * 角色目录（控制平面，所有租户相同，运行期只读）。角色拥有哪些权限只由表数据决定。
 * 表里出现代码不认识的权限码时直接报错，而不是悄悄忽略。
 */
@Repository
public class RoleCatalogue {

    private final JdbcClient jdbc;

    public RoleCatalogue(JdbcClient jdbc) {
        this.jdbc = jdbc;
    }

    public List<RoleDefinition> all() {
        Map<String, Set<Permission>> permissions = permissionsByRole();
        return jdbc.sql("SELECT code, tenant_wide_only, max_grant_hours, requires_seat FROM access_role ORDER BY code")
                .query((rs, n) -> new RoleDefinition(
                        rs.getString("code"),
                        rs.getBoolean("tenant_wide_only"),
                        (Integer) rs.getObject("max_grant_hours"),
                        rs.getBoolean("requires_seat"),
                        permissions.getOrDefault(rs.getString("code"), Set.of())))
                .list();
    }

    public Optional<RoleDefinition> find(String code) {
        return all().stream().filter(role -> role.code().equals(code)).findFirst();
    }

    private Map<String, Set<Permission>> permissionsByRole() {
        Map<String, Set<Permission>> result = new HashMap<>();
        jdbc.sql("SELECT role_code, permission_code FROM access_role_permission")
                .query((rs, n) -> Map.entry(rs.getString("role_code"), toPermission(rs.getString("permission_code"))))
                .list()
                .forEach(e -> result.computeIfAbsent(e.getKey(), k -> new HashSet<>()).add(e.getValue()));
        return result;
    }

    static Permission toPermission(String code) {
        return Permission.fromCode(code)
                .orElseThrow(() -> new IllegalStateException("permission in catalogue is unknown to code: " + code));
    }
}
