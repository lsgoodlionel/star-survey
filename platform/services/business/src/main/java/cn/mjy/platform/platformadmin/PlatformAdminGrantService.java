package cn.mjy.platform.platformadmin;

import java.util.UUID;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

/** The request never supplies the actor; the database rechecks the signed session and grant. */
@Service
class PlatformAdminGrantService {
    private final JdbcClient jdbc;
    private final PlatformAdminActorResolver actors;
    PlatformAdminGrantService(JdbcClient jdbc) {
        this.jdbc = jdbc;
        this.actors = new PlatformAdminActorResolver(new PlatformAdminAuthorization(new JdbcPlatformAdminGrantLookup(jdbc)));
    }

    @Transactional
    boolean change(UUID targetTenant, UUID targetSubject, boolean grant, String reason) {
        var actor = actors.require();
        return jdbc.sql("SELECT platform_admin_change_session(:at,:actor,:sid,:tt,:target,:grant,:trace,:reason)")
                .param("at", actor.tenant()).param("actor", actor.subject()).param("sid", actor.session())
                .param("tt", targetTenant).param("target", targetSubject).param("grant", grant)
                .param("trace", UUID.randomUUID().toString()).param("reason", reason).query(Boolean.class).single();
    }
}
