package cn.mjy.platform.platformadmin;

import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.stereotype.Component;

/** A personnel guard always derives identity from the already-verified security context. */
@Component
public class PlatformPersonnelAccess {
    private final PlatformAdminActorResolver resolver;
    public PlatformPersonnelAccess(JdbcClient jdbc) {
        this.resolver = new PlatformAdminActorResolver(new PlatformAdminAuthorization(new JdbcPlatformAdminGrantLookup(jdbc)));
    }
    public String requireActor() { return resolver.require().subject().toString(); }
}
