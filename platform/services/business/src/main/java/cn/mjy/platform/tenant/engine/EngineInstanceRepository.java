package cn.mjy.platform.tenant.engine;

import cn.mjy.platform.shared.TenantId;
import java.sql.ResultSet;
import java.sql.SQLException;
import java.time.OffsetDateTime;
import java.util.List;
import java.util.Optional;
import java.util.UUID;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.stereotype.Repository;

/**
 * engine_instance 表的读写。必须在 {@code TenantScope} 内调用：查询不带 tenant_id 条件，
 * 可见范围完全由行级安全决定——即使调用方传错了实例标识，也读不到、改不到别的租户的行。
 */
@Repository
public class EngineInstanceRepository {

    private static final String COLUMNS = "id, tenant_id, base_url, status, created_at";

    private final JdbcClient jdbc;

    public EngineInstanceRepository(JdbcClient jdbc) {
        this.jdbc = jdbc;
    }

    public void insert(TenantId tenant, String id, String baseUrl) {
        jdbc.sql("INSERT INTO engine_instance (id, tenant_id, base_url, status) VALUES (:id, :tenant, :url, :status)")
                .param("id", id)
                .param("tenant", tenant.value())
                .param("url", baseUrl)
                .param("status", EngineInstanceStatus.ACTIVE.code())
                .update();
    }

    public Optional<EngineInstance> find(String id) {
        return jdbc.sql("SELECT " + COLUMNS + " FROM engine_instance WHERE id = :id")
                .param("id", id)
                .query(EngineInstanceRepository::map)
                .optional();
    }

    public List<EngineInstance> listAll() {
        return jdbc.sql("SELECT " + COLUMNS + " FROM engine_instance ORDER BY created_at, id")
                .query(EngineInstanceRepository::map)
                .list();
    }

    /** 带前置状态的条件更新，返回是否更新到了行。 */
    public boolean updateStatus(String id, EngineInstanceStatus from, EngineInstanceStatus to) {
        return jdbc.sql("UPDATE engine_instance SET status = :to WHERE id = :id AND status = :from")
                .param("to", to.code())
                .param("id", id)
                .param("from", from.code())
                .update() == 1;
    }

    private static EngineInstance map(ResultSet rs, int row) throws SQLException {
        return new EngineInstance(
                rs.getString("id"),
                new TenantId(rs.getObject("tenant_id", UUID.class)),
                rs.getString("base_url"),
                EngineInstanceStatus.fromCode(rs.getString("status")),
                rs.getObject("created_at", OffsetDateTime.class));
    }
}
