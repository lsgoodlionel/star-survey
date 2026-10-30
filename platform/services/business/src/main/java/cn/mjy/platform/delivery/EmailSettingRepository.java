package cn.mjy.platform.delivery;

import cn.mjy.platform.shared.TenantId;
import java.time.Instant;
import java.sql.Timestamp;
import java.util.Optional;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.stereotype.Repository;

/** 加密 SMTP 配置的窄存储端口；所有调用方必须已进入对应 TenantScope。 */
@Repository
class EmailSettingRepository {
    private final JdbcClient jdbc;

    EmailSettingRepository(JdbcClient jdbc) {
        this.jdbc = jdbc;
    }

    Optional<Row> find(TenantId tenant) {
        return jdbc.sql("""
                SELECT provider, auth_mode, from_address, username, credential_ciphertext, credential_nonce, updated_by, updated_at
                  FROM delivery_email_setting WHERE tenant_id = :tenant
                """)
                .param("tenant", tenant.value())
                .query((rs, ignored) -> new Row(rs.getString("provider"), rs.getString("auth_mode"),
                        rs.getString("from_address"), rs.getString("username"),
                        rs.getBytes("credential_ciphertext"), rs.getBytes("credential_nonce"),
                        rs.getString("updated_by"), rs.getTimestamp("updated_at").toInstant()))
                .optional();
    }

    void upsert(TenantId tenant, String provider, String authMode, String fromAddress, String username,
            byte[] ciphertext, byte[] nonce, String actor) {
        jdbc.sql("""
                INSERT INTO delivery_email_setting (tenant_id, provider, auth_mode, from_address, username,
                                                    credential_ciphertext, credential_nonce, updated_by)
                VALUES (:tenant, :provider, :authMode, :fromAddress, :username, :ciphertext, :nonce, :actor)
                ON CONFLICT (tenant_id) DO UPDATE SET provider = EXCLUDED.provider, auth_mode = EXCLUDED.auth_mode,
                    from_address = EXCLUDED.from_address, username = EXCLUDED.username,
                    credential_ciphertext = EXCLUDED.credential_ciphertext, credential_nonce = EXCLUDED.credential_nonce,
                    updated_by = EXCLUDED.updated_by, updated_at = now()
                """)
                .param("tenant", tenant.value()).param("provider", provider).param("authMode", authMode)
                .param("fromAddress", fromAddress).param("username", username)
                .param("ciphertext", ciphertext).param("nonce", nonce).param("actor", actor).update();
    }

    void delete(TenantId tenant) {
        jdbc.sql("DELETE FROM delivery_email_setting WHERE tenant_id = :tenant")
                .param("tenant", tenant.value()).update();
    }

    record Row(String provider, String authMode, String fromAddress, String username, byte[] ciphertext,
            byte[] nonce, String updatedBy, Instant updatedAt) { }
}
