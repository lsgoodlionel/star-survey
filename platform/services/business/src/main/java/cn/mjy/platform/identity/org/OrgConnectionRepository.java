package cn.mjy.platform.identity.org;

import cn.mjy.platform.shared.TenantId;
import java.sql.ResultSet;
import java.sql.SQLException;
import java.util.List;
import java.util.Optional;
import java.util.UUID;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.stereotype.Repository;

/** org_connection 的读写。必须在 {@code TenantScope} 内调用，可见范围由行级安全决定。 */
@Repository
class OrgConnectionRepository {

    private static final String COLUMNS =
            "id, tenant_id, provider, corp_id, app_id, secret_ref, unknown_user_policy, enabled, event_token_ref, "
            + "event_key_ref";

    private final JdbcClient jdbc;

    OrgConnectionRepository(JdbcClient jdbc) {
        this.jdbc = jdbc;
    }

    OrgConnection insert(OrgConnection connection, String createdBy) {
        return jdbc.sql("""
                        INSERT INTO org_connection (id, tenant_id, provider, corp_id, app_id, secret_ref,
                                                    unknown_user_policy, enabled, created_by, event_token_ref,
                                                    event_key_ref)
                        VALUES (:id, :tenant, :provider, :corp, :app, :ref, :policy, :enabled, :by, :eventToken,
                                :eventKey)
                        RETURNING %s
                        """.formatted(COLUMNS))
                .param("id", connection.id())
                .param("tenant", connection.tenantId().value())
                .param("provider", connection.provider().code())
                .param("corp", connection.corpId())
                .param("app", connection.appId())
                .param("ref", connection.secretRef())
                .param("policy", connection.unknownUserPolicy().code())
                .param("enabled", connection.enabled())
                .param("by", createdBy)
                .param("eventToken", connection.eventTokenRef(), java.sql.Types.VARCHAR)
                .param("eventKey", connection.eventKeyRef(), java.sql.Types.VARCHAR)
                .query(OrgConnectionRepository::map)
                .single();
    }

    Optional<OrgConnection> find(UUID id) {
        return jdbc.sql("SELECT " + COLUMNS + " FROM org_connection WHERE id = :id")
                .param("id", id)
                .query(OrgConnectionRepository::map)
                .optional();
    }

    List<OrgConnection> list() {
        return jdbc.sql("SELECT " + COLUMNS + " FROM org_connection ORDER BY created_at, id")
                .query(OrgConnectionRepository::map)
                .list();
    }

    OrgConnection update(OrgConnection connection) {
        return jdbc.sql("""
                        UPDATE org_connection
                        SET secret_ref = :ref, unknown_user_policy = :policy, enabled = :enabled,
                            event_token_ref = :eventToken, event_key_ref = :eventKey, updated_at = now()
                        WHERE id = :id
                        RETURNING %s
                        """.formatted(COLUMNS))
                .param("id", connection.id())
                .param("ref", connection.secretRef())
                .param("policy", connection.unknownUserPolicy().code())
                .param("enabled", connection.enabled())
                .param("eventToken", connection.eventTokenRef(), java.sql.Types.VARCHAR)
                .param("eventKey", connection.eventKeyRef(), java.sql.Types.VARCHAR)
                .query(OrgConnectionRepository::map)
                .single();
    }

    private static OrgConnection map(ResultSet rs, int row) throws SQLException {
        return new OrgConnection(
                rs.getObject("id", UUID.class),
                new TenantId(rs.getObject("tenant_id", UUID.class)),
                OrgProvider.fromCode(rs.getString("provider")).orElseThrow(),
                rs.getString("corp_id"),
                rs.getString("app_id"),
                rs.getString("secret_ref"),
                UnknownUserPolicy.fromCode(rs.getString("unknown_user_policy")).orElseThrow(),
                rs.getBoolean("enabled"),
                rs.getString("event_token_ref"),
                rs.getString("event_key_ref"));
    }
}
