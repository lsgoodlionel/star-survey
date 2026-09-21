package cn.mjy.platform.access;

import java.time.Instant;
import java.util.UUID;

/** 一条授权的只读视图。resourceId 为空表示整个租户。 */
public record GrantView(UUID id, String actorId, String roleCode, UUID resourceId, String grantedBy,
        Instant expiresAt) {

    String scopeLabel() {
        return resourceId == null ? "tenant" : resourceId.toString();
    }
}
