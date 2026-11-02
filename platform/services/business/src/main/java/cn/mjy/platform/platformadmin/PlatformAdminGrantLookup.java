package cn.mjy.platform.platformadmin;

import java.util.UUID;

/** Current, independently managed platform grant; lookups must never be cached across requests. */
interface PlatformAdminGrantLookup {
    boolean isActive(UUID identityTenant, UUID subject);
}
