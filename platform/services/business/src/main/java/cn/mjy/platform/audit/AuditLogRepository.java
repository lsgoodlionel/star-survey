package cn.mjy.platform.audit;

import cn.mjy.platform.shared.TenantId;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.stereotype.Repository;

/**
 * 审计日志，只追加：运行期账号对该表只有 INSERT 与 SELECT 权限，
 * 没有 UPDATE/DELETE，因此应用层即使出错也改不了历史审计。
 */
@Repository
public class AuditLogRepository {

    private final JdbcClient jdbc;

    public AuditLogRepository(JdbcClient jdbc) {
        this.jdbc = jdbc;
    }

    public void record(TenantId tenant, String actorId, String action, String resource, String traceId) {
        jdbc.sql("""
                INSERT INTO audit_log (tenant_id, actor_id, action, resource, trace_id)
                VALUES (:tenant, :actor, :action, :resource, :trace)
                """)
                .param("tenant", tenant.value())
                .param("actor", actorId)
                .param("action", action)
                .param("resource", resource)
                .param("trace", traceId)
                .update();
    }

    public long countAll() {
        return jdbc.sql("SELECT COUNT(*) FROM audit_log").query(Long.class).single();
    }
}
