package cn.mjy.platform.engine;

import cn.mjy.platform.engine.InvalidEngineEventException.Reason;
import java.time.Instant;
import java.util.Objects;
import java.util.UUID;

/**
 * 已校验的引擎事件。不含租户：租户由处理方按 {@code engineInstanceId} 查目录得到。
 */
public record EngineEvent(
        UUID eventId,
        EngineEventType type,
        String engineInstanceId,
        long surveyId,
        String generation,
        long responseId,
        String source,
        Instant occurredAt) {

    /** 平台当前理解的信封版本（{@code MjyEventRelay::SCHEMA_VERSION}）。 */
    static final int SUPPORTED_SCHEMA_VERSION = 1;

    public EngineEvent {
        Objects.requireNonNull(eventId, "eventId");
        Objects.requireNonNull(type, "type");
        Objects.requireNonNull(engineInstanceId, "engineInstanceId");
        Objects.requireNonNull(generation, "generation");
        Objects.requireNonNull(source, "source");
        Objects.requireNonNull(occurredAt, "occurredAt");
    }

    /**
     * 把已通过 Bean Validation 的信封转换为领域事件。版本或类型不认识时拒收而不是跳过：
     * 跳过等于静默丢事件，拒收则让引擎保留事件，平台升级后自动补投。
     */
    static EngineEvent from(EngineEventBatch.Envelope envelope) {
        if (envelope.schemaVersion() != SUPPORTED_SCHEMA_VERSION) {
            throw new InvalidEngineEventException(Reason.UNSUPPORTED_SCHEMA_VERSION,
                    "schemaVersion " + envelope.schemaVersion() + " in event " + envelope.eventId());
        }
        EngineEventType type = EngineEventType.fromWire(envelope.eventType())
                .orElseThrow(() -> new InvalidEngineEventException(Reason.UNSUPPORTED_EVENT_TYPE,
                        envelope.eventType() + " in event " + envelope.eventId()));
        Instant occurredAt = OccurredAtParser.parse(envelope.occurredAt())
                .orElseThrow(() -> new InvalidEngineEventException(Reason.INVALID_OCCURRED_AT,
                        "occurredAt of event " + envelope.eventId()));
        return new EngineEvent(envelope.eventId(), type, envelope.engineInstanceId(), envelope.surveyId(),
                envelope.generation(), envelope.responseId(), envelope.source(), occurredAt);
    }

    public ResponseKey responseKey() {
        return new ResponseKey(engineInstanceId, surveyId, generation, responseId);
    }
}
