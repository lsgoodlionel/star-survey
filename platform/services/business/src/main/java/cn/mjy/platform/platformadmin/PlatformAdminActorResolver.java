package cn.mjy.platform.platformadmin;

import java.util.UUID;
import org.springframework.security.access.AccessDeniedException;
import org.springframework.security.core.context.SecurityContextHolder;
import org.springframework.security.oauth2.jwt.Jwt;

/** Resolves an actor only from the verified resource-server security context; never from request data.
 * This is not a write authorization: any eventual mutation must recheck the grant atomically.
 */
final class PlatformAdminActorResolver {
    private final PlatformAdminAuthorization authorization;

    PlatformAdminActorResolver(PlatformAdminAuthorization authorization) {
        this.authorization = authorization;
    }

    Actor require() {
        var authentication = SecurityContextHolder.getContext().getAuthentication();
        if (!authorization.isAdmin(authentication)) throw new AccessDeniedException("platform administrator required");
        Jwt jwt = (Jwt) authentication.getPrincipal();
        return new Actor(UUID.fromString(jwt.getClaimAsString("tenant_id")), UUID.fromString(jwt.getSubject()),
                UUID.fromString(jwt.getClaimAsString("sid")));
    }

    record Actor(UUID tenant, UUID subject, UUID session) { }
}
