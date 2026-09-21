package cn.mjy.platform.shared.tenant;

import cn.mjy.platform.shared.TenantId;
import java.util.Optional;
import java.util.UUID;
import java.util.function.Supplier;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.stereotype.Component;
import org.springframework.transaction.support.TransactionTemplate;

/**
 * 在数据库事务内设定当前租户，交给 PostgreSQL 行级安全强制隔离。
 *
 * 用 {@code set_config(..., true)}（等同 SET LOCAL）：设置只在本事务内有效，
 * 提交或回滚后自动失效，因此不会随连接池把租户串到下一个请求。
 * 不在租户作用域内的查询看不到任何租户数据，也写不进去（失败即关闭）。
 */
@Component
public class TenantScope {

    private static final String TENANT_SETTING = "app.tenant_id";

    private final TransactionTemplate transactions;
    private final JdbcClient jdbc;

    public TenantScope(TransactionTemplate transactions, JdbcClient jdbc) {
        this.transactions = transactions;
        this.jdbc = jdbc;
    }

    public <T> T call(TenantId tenant, Supplier<T> work) {
        return transactions.execute(status -> {
            jdbc.sql("SELECT set_config(:name, :value, true)")
                    .param("name", TENANT_SETTING)
                    .param("value", tenant.value().toString())
                    .query(String.class)
                    .single();
            return work.get();
        });
    }

    public void run(TenantId tenant, Runnable work) {
        call(tenant, () -> {
            work.run();
            return null;
        });
    }

    /** 当前连接上数据库看到的租户；作用域外为空。主要用于测试与诊断。 */
    public Optional<UUID> currentDatabaseTenant() {
        String value = jdbc.sql("SELECT current_setting(:name, true)")
                .param("name", TENANT_SETTING)
                .query(String.class)
                .optional()
                .orElse("");
        return value.isEmpty() ? Optional.empty() : Optional.of(UUID.fromString(value));
    }
}
