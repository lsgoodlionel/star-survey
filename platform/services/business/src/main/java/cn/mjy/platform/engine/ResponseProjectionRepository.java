package cn.mjy.platform.engine;

import cn.mjy.platform.shared.TenantId;
import java.sql.ResultSet;
import java.sql.SQLException;
import java.sql.Types;
import java.time.Instant;
import java.time.OffsetDateTime;
import java.util.Optional;
import java.util.UUID;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.stereotype.Repository;

/**
 * 答卷投影。必须在 {@code TenantScope} 内调用；读取只看得到当前租户的行，
 * 写入的租户若与作用域不符，由行级安全拒绝。
 */
@Repository
public class ResponseProjectionRepository {

    private static final String KEY_PREDICATE = """
            tenant_id = :tenant AND engine_instance_id = :instance AND survey_id = :survey
            AND generation = :generation AND response_id = :response
            """;

    private final JdbcClient jdbc;

    public ResponseProjectionRepository(JdbcClient jdbc) {
        this.jdbc = jdbc;
    }

    /**
     * 用一个事件推动投影。状态只前进：目标状态不比当前靠后时不做任何改动。
     * 已有投影时先加行锁再比较，因此并发事件也不会互相覆盖成更旧的状态。
     *
     * @return 实际发生的状态变化；没有变化时为空
     */
    public Optional<Transition> advance(TenantId tenant, EngineEvent event) {
        ResponseState target = event.type().target();
        if (insertIfAbsent(tenant, event, target)) {
            return Optional.of(new Transition(null, target));
        }
        ResponseState current = lockState(tenant, event.responseKey());
        if (!target.isAfter(current)) {
            return Optional.empty();
        }
        moveTo(tenant, event, target);
        return Optional.of(new Transition(current, target));
    }

    /** 当前租户下的投影；其他租户的投影在行级安全下不可见，同样返回空。 */
    public Optional<ResponseProjection> find(ResponseKey key) {
        return jdbc.sql("""
                        SELECT engine_instance_id, survey_id, generation, response_id, state,
                               first_event_at, completed_at, deleted_at, last_event_id
                        FROM response_projection
                        WHERE engine_instance_id = :instance AND survey_id = :survey
                          AND generation = :generation AND response_id = :response
                        """)
                .param("instance", key.engineInstanceId())
                .param("survey", key.surveyId())
                .param("generation", key.generation())
                .param("response", key.responseId())
                .query(ResponseProjectionRepository::mapRow)
                .optional();
    }

    private boolean insertIfAbsent(TenantId tenant, EngineEvent event, ResponseState target) {
        return jdbc.sql("""
                        INSERT INTO response_projection (tenant_id, engine_instance_id, survey_id, generation,
                                                         response_id, state, first_event_at, completed_at,
                                                         deleted_at, last_event_id)
                        VALUES (:tenant, :instance, :survey, :generation,
                                :response, :state, :occurredAt, :completedAt,
                                :deletedAt, :eventId)
                        ON CONFLICT (tenant_id, engine_instance_id, survey_id, generation, response_id) DO NOTHING
                        """)
                .param("tenant", tenant.value())
                .param("instance", event.engineInstanceId())
                .param("survey", event.surveyId())
                .param("generation", event.generation())
                .param("response", event.responseId())
                .param("state", target.dbValue())
                .param("occurredAt", SqlTime.utc(event.occurredAt()))
                .param("completedAt", timeIf(target == ResponseState.ENGINE_COMPLETED, event), Types.TIMESTAMP_WITH_TIMEZONE)
                .param("deletedAt", timeIf(target == ResponseState.DELETED, event), Types.TIMESTAMP_WITH_TIMEZONE)
                .param("eventId", event.eventId())
                .update() == 1;
    }

    private ResponseState lockState(TenantId tenant, ResponseKey key) {
        String state = jdbc.sql("SELECT state FROM response_projection WHERE " + KEY_PREDICATE + " FOR UPDATE")
                .param("tenant", tenant.value())
                .param("instance", key.engineInstanceId())
                .param("survey", key.surveyId())
                .param("generation", key.generation())
                .param("response", key.responseId())
                .query(String.class)
                .single();
        return ResponseState.fromDb(state);
    }

    /** 已完成的答卷随后被删除时保留原完成时刻：两个时间戳都是事实，互不覆盖。 */
    private void moveTo(TenantId tenant, EngineEvent event, ResponseState target) {
        ResponseKey key = event.responseKey();
        jdbc.sql("""
                        UPDATE response_projection
                        SET state = :state,
                            completed_at = COALESCE(completed_at, :completedAt),
                            deleted_at = COALESCE(deleted_at, :deletedAt),
                            last_event_id = :eventId,
                            updated_at = now()
                        WHERE\s""" + KEY_PREDICATE)
                .param("state", target.dbValue())
                .param("completedAt", timeIf(target == ResponseState.ENGINE_COMPLETED, event), Types.TIMESTAMP_WITH_TIMEZONE)
                .param("deletedAt", timeIf(target == ResponseState.DELETED, event), Types.TIMESTAMP_WITH_TIMEZONE)
                .param("eventId", event.eventId())
                .param("tenant", tenant.value())
                .param("instance", key.engineInstanceId())
                .param("survey", key.surveyId())
                .param("generation", key.generation())
                .param("response", key.responseId())
                .update();
    }

    private static OffsetDateTime timeIf(boolean condition, EngineEvent event) {
        return condition ? SqlTime.utc(event.occurredAt()) : null;
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
