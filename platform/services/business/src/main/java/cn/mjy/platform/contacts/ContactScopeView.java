package cn.mjy.platform.contacts;

import java.time.Instant;
import java.util.UUID;

/** 部门级数据范围：只收窄，不放宽（ADR 0017 决定 4）。 */
public record ContactScopeView(UUID id, String actorId, UUID orgUnitId, Instant expiresAt) {
}
