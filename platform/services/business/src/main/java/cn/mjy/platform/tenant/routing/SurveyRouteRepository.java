package cn.mjy.platform.tenant.routing;

import cn.mjy.platform.shared.TenantId;
import java.sql.ResultSet;
import java.sql.SQLException;
import java.util.Optional;
import java.util.UUID;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.stereotype.Repository;

/**
 * survey_route 表的读写。必须在 {@code TenantScope} 内调用，可见范围由行级安全决定。
 * 一个公开 UUID 可以有多条路由（每次重新发布一条），其中 superseded_at 为空的一条是当前路由（ADR 0012）。
 */
@Repository
public class SurveyRouteRepository {

    private static final String COLUMNS = "public_id, tenant_id, engine_instance_id, engine_sid";

    private final JdbcClient jdbc;

    public SurveyRouteRepository(JdbcClient jdbc) {
        this.jdbc = jdbc;
    }

    /**
     * 插入新路由；同一 (实例, sid) 已存在时什么都不做并返回空。
     * 复合外键保证实例属于同一租户，所以冲突的既有行一定也是本租户的。
     */
    public Optional<SurveyRoute> insertIfAbsent(TenantId tenant, String instanceId, int sid) {
        return jdbc.sql("""
                        INSERT INTO survey_route (public_id, tenant_id, engine_instance_id, engine_sid)
                        VALUES (:id, :tenant, :instance, :sid)
                        ON CONFLICT (engine_instance_id, engine_sid) DO NOTHING
                        RETURNING %s
                        """.formatted(COLUMNS))
                .param("id", UUID.randomUUID())
                .param("tenant", tenant.value())
                .param("instance", instanceId)
                .param("sid", sid)
                .query(SurveyRouteRepository::map)
                .optional();
    }

    /**
     * 以调用方给定的公开 UUID 插入路由；公开 UUID 或 (实例, sid) 任一已存在时什么都不做并返回空，
     * 由调用方判断既有行是否就是同一条路由。
     */
    public Optional<SurveyRoute> insertWithPublicIdIfAbsent(TenantId tenant, UUID publicId, String instanceId, int sid) {
        return jdbc.sql("""
                        INSERT INTO survey_route (public_id, tenant_id, engine_instance_id, engine_sid)
                        VALUES (:id, :tenant, :instance, :sid)
                        ON CONFLICT DO NOTHING
                        RETURNING %s
                        """.formatted(COLUMNS))
                .param("id", publicId)
                .param("tenant", tenant.value())
                .param("instance", instanceId)
                .param("sid", sid)
                .query(SurveyRouteRepository::map)
                .optional();
    }

    /** 公开 UUID 的当前路由（被重新发布取代的旧路由不算，ADR 0012）。 */
    public Optional<SurveyRoute> findByPublicId(UUID publicId) {
        return jdbc.sql("SELECT " + COLUMNS + " FROM survey_route WHERE public_id = :id AND superseded_at IS NULL")
                .param("id", publicId)
                .query(SurveyRouteRepository::map)
                .optional();
    }

    /** 同上，但对这一行加锁：切换路由时与并发的切换串行化。 */
    public Optional<SurveyRoute> lockCurrent(UUID publicId) {
        return jdbc.sql("SELECT " + COLUMNS
                        + " FROM survey_route WHERE public_id = :id AND superseded_at IS NULL FOR UPDATE")
                .param("id", publicId)
                .query(SurveyRouteRepository::map)
                .optional();
    }

    /** 把 (实例, sid) 这条当前路由标记为被取代；返回是否确实标记了一行。 */
    public boolean supersede(UUID publicId, String instanceId, int sid) {
        return jdbc.sql("""
                        UPDATE survey_route SET superseded_at = now()
                         WHERE public_id = :id AND engine_instance_id = :instance AND engine_sid = :sid
                           AND superseded_at IS NULL
                        """)
                .param("id", publicId)
                .param("instance", instanceId)
                .param("sid", sid)
                .update() == 1;
    }

    /** 按 (实例, sid) 反查，包括已被取代的路由：旧版本的答卷仍属于同一个公开 UUID。 */
    public Optional<SurveyRoute> findByEngine(String instanceId, int sid) {
        return jdbc.sql("SELECT " + COLUMNS + " FROM survey_route WHERE engine_instance_id = :instance AND engine_sid = :sid")
                .param("instance", instanceId)
                .param("sid", sid)
                .query(SurveyRouteRepository::map)
                .optional();
    }

    private static SurveyRoute map(ResultSet rs, int row) throws SQLException {
        return new SurveyRoute(
                rs.getObject("public_id", UUID.class),
                new TenantId(rs.getObject("tenant_id", UUID.class)),
                rs.getString("engine_instance_id"),
                rs.getInt("engine_sid"));
    }
}
