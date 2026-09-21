package cn.mjy.platform.access;

import java.time.Instant;
import java.util.UUID;

/**
 * 授权请求。resourceId 为空表示整个租户；expiresAt 为空表示不过期
 * （角色要求限时的，如支持人员，必须给出到期时间）。
 */
public record GrantRequest(String actorId, String roleCode, UUID resourceId, Instant expiresAt) {

    public GrantRequest {
        if (actorId == null || actorId.isBlank()) {
            throw new IllegalArgumentException("actorId is required");
        }
        if (roleCode == null || roleCode.isBlank()) {
            throw new IllegalArgumentException("roleCode is required");
        }
    }
}
