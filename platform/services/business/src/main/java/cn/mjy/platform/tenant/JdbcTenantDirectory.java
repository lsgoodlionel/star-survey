package cn.mjy.platform.tenant;

import cn.mjy.platform.shared.TenantId;
import cn.mjy.platform.shared.tenant.TenantDirectory;
import java.util.List;
import java.util.UUID;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.stereotype.Component;

/** 租户登记表属于控制平面（无行级隔离，运行期账号只读），这里只读出标识，不读名称等其他列。 */
@Component
public class JdbcTenantDirectory implements TenantDirectory {

    private final JdbcClient jdbc;

    public JdbcTenantDirectory(JdbcClient jdbc) {
        this.jdbc = jdbc;
    }

    @Override
    public List<TenantId> openTenants() {
        return jdbc.sql("SELECT id FROM tenant WHERE status <> :closed ORDER BY created_at, id")
                .param("closed", TenantStatus.CLOSED.code())
                .query((rs, row) -> new TenantId(rs.getObject("id", UUID.class)))
                .list();
    }
}
