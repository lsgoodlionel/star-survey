package cn.mjy.platform.access;

import cn.mjy.platform.shared.TenantId;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.stereotype.Repository;

/** 租户级访问设置。须在租户作用域内调用。 */
@Repository
class AccessSettingsRepository {

    /** 没有设置行时的默认值：需要审核（保守）。 */
    static final boolean DEFAULT_PUBLISH_APPROVAL_REQUIRED = true;

    private final JdbcClient jdbc;

    AccessSettingsRepository(JdbcClient jdbc) {
        this.jdbc = jdbc;
    }

    boolean publishApprovalRequired(TenantId tenant) {
        return jdbc.sql("SELECT publish_approval_required FROM access_tenant_settings WHERE tenant_id = :tenant")
                .param("tenant", tenant.value())
                .query(Boolean.class)
                .optional()
                .orElse(DEFAULT_PUBLISH_APPROVAL_REQUIRED);
    }

    void setPublishApprovalRequired(TenantId tenant, boolean required, String updatedBy) {
        jdbc.sql("""
                INSERT INTO access_tenant_settings (tenant_id, publish_approval_required, updated_by)
                VALUES (:tenant, :required, :by)
                ON CONFLICT (tenant_id) DO UPDATE
                    SET publish_approval_required = EXCLUDED.publish_approval_required,
                        updated_by = EXCLUDED.updated_by, updated_at = now()
                """)
                .param("tenant", tenant.value())
                .param("required", required)
                .param("by", updatedBy)
                .update();
    }
}
