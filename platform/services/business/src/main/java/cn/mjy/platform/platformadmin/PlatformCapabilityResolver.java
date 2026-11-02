package cn.mjy.platform.platformadmin;

import java.util.Map;
import org.springframework.security.core.context.SecurityContextHolder;

/** Uncached capability snapshot; mutation endpoints must independently authorize every request. */
final class PlatformCapabilityResolver {
    private final PlatformAdminAuthorization authorization;
    PlatformCapabilityResolver(PlatformAdminAuthorization authorization) { this.authorization = authorization; }
    Map<String, Boolean> current() {
        return Map.of("managePlatform", authorization.isAdmin(SecurityContextHolder.getContext().getAuthentication()));
    }
}
