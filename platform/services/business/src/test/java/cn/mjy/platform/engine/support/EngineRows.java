package cn.mjy.platform.engine.support;

import cn.mjy.platform.shared.TenantId;
import cn.mjy.platform.shared.tenant.TenantScope;
import java.util.List;
import org.springframework.jdbc.core.simple.JdbcClient;

/**
 * 测试断言用的只读查询。一律在指定租户的作用域内执行，因此看到的就是该租户在行级安全下能看到的行。
 */
public class EngineRows {

    private final TenantScope scope;
    private final JdbcClient jdbc;

    public EngineRows(TenantScope scope, JdbcClient jdbc) {
        this.scope = scope;
        this.jdbc = jdbc;
    }

    public long inbox(TenantId tenant) {
        return count(tenant, "SELECT COUNT(*) FROM engine_event_inbox");
    }

    public long projections(TenantId tenant) {
        return count(tenant, "SELECT COUNT(*) FROM response_projection");
    }

    public long outbox(TenantId tenant) {
        return count(tenant, "SELECT COUNT(*) FROM engine_outbox");
    }

    public List<String> outboxTypes(TenantId tenant) {
        return scope.call(tenant, () -> jdbc.sql("SELECT event_type FROM engine_outbox ORDER BY id")
                .query(String.class).list());
    }

    /** 某租户、某实例下某份答卷（按代次）的投影状态；没有投影时为空列表。 */
    public List<String> states(TenantId tenant, String instance, long survey, String generation, long response) {
        return scope.call(tenant, () -> jdbc.sql("""
                        SELECT state FROM response_projection
                        WHERE engine_instance_id = :instance AND survey_id = :survey
                          AND generation = :generation AND response_id = :response
                        """)
                .param("instance", instance)
                .param("survey", survey)
                .param("generation", generation)
                .param("response", response)
                .query(String.class).list());
    }

    private long count(TenantId tenant, String sql) {
        return scope.call(tenant, () -> jdbc.sql(sql).query(Long.class).single());
    }
}
