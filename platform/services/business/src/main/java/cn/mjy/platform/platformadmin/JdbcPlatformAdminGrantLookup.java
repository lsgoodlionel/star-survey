package cn.mjy.platform.platformadmin;

import java.util.UUID;
import org.springframework.jdbc.core.simple.JdbcClient;

/** Read directly from the authorization store for every request; no local permission cache. */
final class JdbcPlatformAdminGrantLookup implements PlatformAdminGrantLookup {
    private final JdbcClient jdbc;
    JdbcPlatformAdminGrantLookup(JdbcClient jdbc) { this.jdbc = jdbc; }
    @Override public boolean isActive(UUID tenant, UUID subject) {
        return jdbc.sql("SELECT platform_admin_active(:tenant, :subject)")
                .param("tenant", tenant).param("subject", subject).query(Boolean.class).single();
    }
}
