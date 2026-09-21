package cn.mjy.platform.shared;

import java.util.List;
import java.util.Objects;

/**
 * 一次请求的可信上下文（方案 §5 共享契约）。只由身份层根据已验签的令牌构造，
 * 业务模块只读取，不得自行拼装。
 */
public record TenantContext(TenantId tenantId, String actorId, List<String> roles, String traceId) {

    public TenantContext {
        Objects.requireNonNull(tenantId, "tenantId");
        Objects.requireNonNull(actorId, "actorId");
        roles = List.copyOf(roles);
        Objects.requireNonNull(traceId, "traceId");
    }

    public boolean hasRole(String role) {
        return roles.contains(role);
    }
}
