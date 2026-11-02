package cn.mjy.platform.platformadmin;

import java.util.UUID;
import org.springframework.security.core.Authentication;
import org.springframework.security.oauth2.jwt.Jwt;

/**
 * Personnel identity is restricted to the existing organisation login issuer and live-session JWTs.
 * This policy must only be called after the resource server has verified signature, expiry, and sid.
 * External IdP tokens need a separately verified issuer/audience policy before being supported here.
 */
final class PlatformAdminAuthorization {
    private final PlatformAdminGrantLookup grants;
    PlatformAdminAuthorization(PlatformAdminGrantLookup grants) { this.grants = grants; }

    boolean isAdmin(Authentication authentication) {
        if (authentication == null || !authentication.isAuthenticated()
                || !(authentication.getPrincipal() instanceof Jwt jwt)
                || !"mjy-platform".equals(jwt.getClaimAsString("iss"))) return false;
        try {
            UUID tenant = UUID.fromString(jwt.getClaimAsString("tenant_id"));
            UUID subject = UUID.fromString(jwt.getSubject());
            UUID.fromString(jwt.getClaimAsString("sid"));
            return grants.isActive(tenant, subject);
        } catch (RuntimeException e) {
            return false;
        }
    }
}
