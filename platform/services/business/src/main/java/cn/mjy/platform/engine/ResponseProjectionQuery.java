package cn.mjy.platform.engine;

import java.sql.ResultSet;
import java.sql.SQLException;
import java.time.Instant;
import java.time.OffsetDateTime;
import java.util.EnumMap;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import java.util.UUID;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.stereotype.Repository;

/**
 * 答卷投影的只读查询，供答卷查询模块（response）使用——其他模块不直接读投影表（CONVENTIONS）。
 * 必须在 {@code TenantScope} 内调用：行级安全保证只看得到当前租户的行，
 * 即使调用方传入别的租户的 (实例, sid) 也一行都查不到。
 *
 * <p>翻页按主键后缀 {@code (generation, response_id)} 做键集分页，走投影主键索引。
 */
@Repository
public class ResponseProjectionQuery {

    /** 键集位置：上一页最后一行的 (代次, 答卷号)。 */
    public record Position(String generation, long responseId) {
    }

    private static final String COLUMNS = """
            engine_instance_id, survey_id, generation, response_id, state,
            first_event_at, completed_at, deleted_at, last_event_id""";

    private final JdbcClient jdbc;

    public ResponseProjectionQuery(JdbcClient jdbc) {
        this.jdbc = jdbc;
    }

    /**
     * 某个 (实例, sid) 下、位置之后的至多 limit 行，按 (代次, 答卷号) 升序。
     *
     * @param state 只要该状态的行；{@code null} 表示全部状态
     * @param after 从该位置之后开始；{@code null} 表示从头开始
     */
    public List<ResponseProjection> page(String engineInstanceId, long engineSid, ResponseState state,
            Position after, int limit) {
        return page(engineInstanceId, engineSid, state, after, limit, null);
    }

    /**
     * 同 {@link #page(String, long, ResponseState, Position, int)}，另加水位线：只要平台首次写入投影的时刻
     * （{@code created_at}）不晚于 createdAtOrBefore 的行。答卷导出用它把快照固定在作业创建那一刻（ADR 0015）。
     *
     * @param createdAtOrBefore 水位线；{@code null} 表示不限
     */
    public List<ResponseProjection> page(String engineInstanceId, long engineSid, ResponseState state,
            Position after, int limit, OffsetDateTime createdAtOrBefore) {
        if (limit <= 0) {
            throw new IllegalArgumentException("limit must be positive");
        }
        // 条件按是否给出分别拼接固定的 SQL 片段（不含任何调用方文本），让键集条件能直接用上主键索引。
        String sql = "SELECT " + COLUMNS + " FROM response_projection"
                + " WHERE engine_instance_id = :instance AND survey_id = :sid"
                + (state != null ? " AND state = :state" : "")
                + (after != null ? " AND (generation, response_id) > (:generation, :responseId)" : "")
                + (createdAtOrBefore != null ? " AND created_at <= :watermark" : "")
                + " ORDER BY generation, response_id LIMIT :limit";
        JdbcClient.StatementSpec statement = jdbc.sql(sql)
                .param("instance", engineInstanceId)
                .param("sid", engineSid)
                .param("limit", limit);
        if (state != null) {
            statement = statement.param("state", state.dbValue());
        }
        if (after != null) {
            statement = statement.param("generation", after.generation()).param("responseId", after.responseId());
        }
        if (createdAtOrBefore != null) {
            statement = statement.param("watermark", createdAtOrBefore);
        }
        return statement.query(ResponseProjectionQuery::mapRow).list();
    }

    /** 各状态的答卷数；没有答卷的状态不出现在结果里。 */
    public Map<ResponseState, Long> countByState(String engineInstanceId, long engineSid) {
        Map<ResponseState, Long> counts = new EnumMap<>(ResponseState.class);
        jdbc.sql("""
                        SELECT state, COUNT(*) AS n FROM response_projection
                         WHERE engine_instance_id = :instance AND survey_id = :sid
                         GROUP BY state
                        """)
                .param("instance", engineInstanceId)
                .param("sid", engineSid)
                .query((rs, n) -> Map.entry(ResponseState.fromDb(rs.getString("state")), rs.getLong("n")))
                .list()
                .forEach(e -> counts.put(e.getKey(), e.getValue()));
        return Map.copyOf(counts);
    }

    /**
     * 最近出现的答卷表代次：按每个代次最晚的首事件时间取最大者（ADR 0013 决定 3）。
     * 引擎当前答卷表只对应这个代次，其余代次的答卷号不能拿去引擎里查。
     */
    public Optional<String> latestGeneration(String engineInstanceId, long engineSid) {
        return jdbc.sql("""
                        SELECT generation FROM response_projection
                         WHERE engine_instance_id = :instance AND survey_id = :sid
                         GROUP BY generation
                         ORDER BY MAX(first_event_at) DESC, generation DESC
                         LIMIT 1
                        """)
                .param("instance", engineInstanceId)
                .param("sid", engineSid)
                .query(String.class)
                .optional();
    }

    private static ResponseProjection mapRow(ResultSet rs, int rowNum) throws SQLException {
        ResponseKey key = new ResponseKey(rs.getString("engine_instance_id"), rs.getLong("survey_id"),
                rs.getString("generation"), rs.getLong("response_id"));
        return new ResponseProjection(key, ResponseState.fromDb(rs.getString("state")),
                instant(rs, "first_event_at"), instant(rs, "completed_at"), instant(rs, "deleted_at"),
                rs.getObject("last_event_id", UUID.class));
    }

    private static Instant instant(ResultSet rs, String column) throws SQLException {
        OffsetDateTime value = rs.getObject(column, OffsetDateTime.class);
        return value == null ? null : value.toInstant();
    }
}
