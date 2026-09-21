package cn.mjy.platform.tenant.engine;

import cn.mjy.platform.shared.TenantId;
import cn.mjy.platform.shared.engine.EngineInstanceDirectory;
import java.util.Optional;
import java.util.UUID;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.stereotype.Component;

/**
 * 按实例标识反查所属租户。调用方（引擎事件接收）此时还不知道租户，而 engine_instance 受行级安全保护，
 * 所以查询走数据库函数 {@code engine_instance_tenant}（SECURITY DEFINER，见 V101）：
 * 它只回答"这个实例属于哪个租户"这一件事，且只在实例与租户都为 active 时回答，不暴露任何列表。
 */
@Component
public class JdbcEngineInstanceDirectory implements EngineInstanceDirectory {

    private final JdbcClient jdbc;

    public JdbcEngineInstanceDirectory(JdbcClient jdbc) {
        this.jdbc = jdbc;
    }

    @Override
    public Optional<TenantId> tenantOf(String engineInstanceId) {
        if (!EngineInstance.isWellFormedId(engineInstanceId)) {
            return Optional.empty();
        }
        return jdbc.sql("SELECT engine_instance_tenant(:id)")
                .param("id", engineInstanceId)
                .query((rs, row) -> Optional.ofNullable(rs.getObject(1, UUID.class)))
                .single()
                .map(TenantId::new);
    }
}
