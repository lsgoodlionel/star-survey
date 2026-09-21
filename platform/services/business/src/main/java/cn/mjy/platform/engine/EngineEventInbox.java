package cn.mjy.platform.engine;

import cn.mjy.platform.shared.TenantId;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.stereotype.Repository;

/**
 * 收件箱，只追加。必须在 {@code TenantScope} 内调用；写入的租户若与作用域不符，由行级安全拒绝。
 */
@Repository
public class EngineEventInbox {

    private final JdbcClient jdbc;

    public EngineEventInbox(JdbcClient jdbc) {
        this.jdbc = jdbc;
    }

    /**
     * 记录一个事件。并发的同一事件会在主键上排队，后到者等先到者提交后判为重复。
     *
     * @return {@code true} 首次收到；{@code false} 此前已收到（重复投递）
     */
    public boolean record(TenantId tenant, EngineEvent event) {
        int inserted = jdbc.sql("""
                INSERT INTO engine_event_inbox (tenant_id, engine_instance_id, event_id, event_type, schema_version,
                                                survey_id, generation, response_id, source, occurred_at)
                VALUES (:tenant, :instance, :eventId, :type, :schemaVersion,
                        :survey, :generation, :response, :source, :occurredAt)
                ON CONFLICT (tenant_id, engine_instance_id, event_id) DO NOTHING
                """)
                .param("tenant", tenant.value())
                .param("instance", event.engineInstanceId())
                .param("eventId", event.eventId())
                .param("type", event.type().wireName())
                .param("schemaVersion", EngineEvent.SUPPORTED_SCHEMA_VERSION)
                .param("survey", event.surveyId())
                .param("generation", event.generation())
                .param("response", event.responseId())
                .param("source", event.source())
                .param("occurredAt", SqlTime.utc(event.occurredAt()))
                .update();
        return inserted == 1;
    }
}
