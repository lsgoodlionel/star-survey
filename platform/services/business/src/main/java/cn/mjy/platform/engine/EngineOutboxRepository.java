package cn.mjy.platform.engine;

import cn.mjy.platform.shared.TenantId;
import java.sql.Types;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.stereotype.Repository;

/**
 * 事务性发件箱。必须与投影变更在同一个 {@code TenantScope} 事务里调用，
 * 投影回滚时消息一并回滚。本车道只写，不实现消费者。
 */
@Repository
public class EngineOutboxRepository {

    private final JdbcClient jdbc;

    public EngineOutboxRepository(JdbcClient jdbc) {
        this.jdbc = jdbc;
    }

    /**
     * 为一次投影状态变化写一条消息。消息体自描述（含租户），发布者无需回查投影。
     *
     * @throws IllegalArgumentException 该状态不需要通知下游（例如进入 in_progress）
     */
    public void append(TenantId tenant, EngineEvent event, Transition transition) {
        String eventType = transition.to().outboxEventType()
                .orElseThrow(() -> new IllegalArgumentException("no outbox event for state " + transition.to()));
        String previous = transition.from() == null ? null : transition.from().dbValue();
        jdbc.sql("""
                INSERT INTO engine_outbox (tenant_id, event_type, engine_instance_id, survey_id, generation,
                                           response_id, previous_state, source_event_id, occurred_at, payload)
                VALUES (:tenant, :eventType, :instance, :survey, :generation,
                        :response, :previous, :eventId, :occurredAt,
                        jsonb_build_object(
                            'tenantId', CAST(:tenant AS text),
                            'eventType', CAST(:eventType AS text),
                            'engineInstanceId', CAST(:instance AS text),
                            'surveyId', CAST(:survey AS bigint),
                            'generation', CAST(:generation AS text),
                            'responseId', CAST(:response AS bigint),
                            'state', CAST(:state AS text),
                            'previousState', CAST(:previous AS text),
                            'occurredAt', CAST(:occurredAtIso AS text),
                            'source', CAST(:source AS text),
                            'sourceEventId', CAST(:eventId AS text)))
                """)
                .param("tenant", tenant.value())
                .param("eventType", eventType)
                .param("instance", event.engineInstanceId())
                .param("survey", event.surveyId())
                .param("generation", event.generation())
                .param("response", event.responseId())
                .param("previous", previous, Types.VARCHAR)
                .param("eventId", event.eventId())
                .param("occurredAt", SqlTime.utc(event.occurredAt()))
                .param("state", transition.to().dbValue())
                .param("occurredAtIso", event.occurredAt().toString())
                .param("source", event.source())
                .update();
    }
}
