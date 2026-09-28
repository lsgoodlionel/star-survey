package cn.mjy.platform.engine;

import java.sql.ResultSet;
import java.sql.SQLException;
import java.time.Instant;
import java.time.OffsetDateTime;
import java.util.ArrayList;
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

    /** 一个 WHERE 片段及其占位参数。片段是固定文本，永不含调用方提供的字符串。 */
    private record Condition(String sql, Map<String, Object> params) {
    }

    private static final String COLUMNS = """
            engine_instance_id, survey_id, generation, response_id, state,
            first_event_at, completed_at, deleted_at, last_event_id""";

    private final JdbcClient jdbc;

    public ResponseProjectionQuery(JdbcClient jdbc) {
        this.jdbc = jdbc;
    }

    /**
     * 某个 (实例, sid) 下、位置之后、命中筛选条件的至多 limit 行，按 (代次, 答卷号) 升序。
     *
     * <p>条件全部下推到这条 SQL：页因此永远是满的（不足才是最后一页），
     * 调用方不需要"取一页再扔掉几行"，键集游标的含义也不变（R06-01，ADR 0013 增补一）。
     *
     * @param filter 条件；{@link ProjectionFilter#ALL} 表示不限
     * @param after  从该位置之后开始；{@code null} 表示从头开始
     */
    public List<ResponseProjection> page(String engineInstanceId, long engineSid, ProjectionFilter filter,
            Position after, int limit) {
        if (limit <= 0) {
            throw new IllegalArgumentException("limit must be positive");
        }
        List<Condition> conditions = conditions(filter == null ? ProjectionFilter.ALL : filter, after);
        StringBuilder sql = new StringBuilder("SELECT ").append(COLUMNS)
                .append(" FROM response_projection WHERE engine_instance_id = :instance AND survey_id = :sid");
        conditions.forEach(condition -> sql.append(" AND ").append(condition.sql()));
        sql.append(" ORDER BY generation, response_id LIMIT :limit");

        JdbcClient.StatementSpec statement = jdbc.sql(sql.toString())
                .param("instance", engineInstanceId)
                .param("sid", engineSid)
                .param("limit", limit);
        for (Condition condition : conditions) {
            for (Map.Entry<String, Object> param : condition.params().entrySet()) {
                statement = statement.param(param.getKey(), param.getValue());
            }
        }
        return statement.query(ResponseProjectionQuery::mapRow).list();
    }

    /**
     * 给出的条件各拼一个固定 SQL 片段（不含任何调用方文本）。键集条件排在最后，
     * 与 {@code ORDER BY} 一致，走投影主键索引。
     */
    private static List<Condition> conditions(ProjectionFilter filter, Position after) {
        List<Condition> conditions = new ArrayList<>();
        add(conditions, "state = :state", "state",
                filter.state() == null ? null : filter.state().dbValue());
        add(conditions, "first_event_at >= :startedFrom", "startedFrom", SqlTime.utc(filter.startedFrom()));
        add(conditions, "first_event_at <= :startedTo", "startedTo", SqlTime.utc(filter.startedTo()));
        add(conditions, "completed_at >= :completedFrom", "completedFrom", SqlTime.utc(filter.completedFrom()));
        add(conditions, "completed_at <= :completedTo", "completedTo", SqlTime.utc(filter.completedTo()));
        add(conditions, "created_at <= :watermark", "watermark", SqlTime.utc(filter.createdAtOrBefore()));
        if (after != null) {
            conditions.add(new Condition("(generation, response_id) > (:generation, :responseId)",
                    Map.of("generation", after.generation(), "responseId", after.responseId())));
        }
        return List.copyOf(conditions);
    }

    private static void add(List<Condition> conditions, String sql, String param, Object value) {
        if (value != null) {
            conditions.add(new Condition(sql, Map.of(param, value)));
        }
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
