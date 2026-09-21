package cn.mjy.platform.tenant;

import cn.mjy.platform.shared.TenantId;
import java.sql.ResultSet;
import java.sql.SQLException;
import java.time.OffsetDateTime;
import java.util.Optional;
import java.util.UUID;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.stereotype.Repository;

/** 租户登记表的读写。运行期账号只有 SELECT、INSERT 与 status 列的 UPDATE 权限。 */
@Repository
public class TenantRepository {

    private final JdbcClient jdbc;

    public TenantRepository(JdbcClient jdbc) {
        this.jdbc = jdbc;
    }

    public void insert(TenantId id, String code, String name) {
        jdbc.sql("INSERT INTO tenant (id, code, name, status) VALUES (:id, :code, :name, :status)")
                .param("id", id.value())
                .param("code", code)
                .param("name", name)
                .param("status", TenantStatus.PROVISIONING.code())
                .update();
    }

    public Optional<Tenant> findById(TenantId id) {
        return jdbc.sql("SELECT id, code, name, status, created_at FROM tenant WHERE id = :id")
                .param("id", id.value())
                .query(TenantRepository::map)
                .optional();
    }

    /** 带前置状态的条件更新：并发下只有一个迁移能成功，返回是否更新到了行。 */
    public boolean updateStatus(TenantId id, TenantStatus from, TenantStatus to) {
        int rows = jdbc.sql("UPDATE tenant SET status = :to WHERE id = :id AND status = :from")
                .param("to", to.code())
                .param("id", id.value())
                .param("from", from.code())
                .update();
        return rows == 1;
    }

    private static Tenant map(ResultSet rs, int row) throws SQLException {
        return new Tenant(
                new TenantId(rs.getObject("id", UUID.class)),
                rs.getString("code"),
                rs.getString("name"),
                TenantStatus.fromCode(rs.getString("status")),
                rs.getObject("created_at", OffsetDateTime.class));
    }
}
