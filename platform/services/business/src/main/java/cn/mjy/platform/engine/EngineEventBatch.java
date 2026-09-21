package cn.mjy.platform.engine;

import jakarta.validation.Valid;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.NotNull;
import jakarta.validation.constraints.Positive;
import jakarta.validation.constraints.Size;
import java.util.List;
import java.util.UUID;

/**
 * 请求体 {@code {"events":[...]}}，字段与 {@code MjyEventRelay::toEnvelope()} 一一对应。
 *
 * <p>刻意没有租户字段：租户只由 {@code engineInstanceId} 经引擎实例目录判定；
 * 发送端夹带的任何其他字段（包括自称的租户）都被忽略。
 */
public record EngineEventBatch(@NotNull @Size(max = EngineEventBatch.MAX_EVENTS) List<@Valid @NotNull Envelope> events) {

    /** 中继每批 100 条；留出余量，但不接受无上限的批次。 */
    static final int MAX_EVENTS = 1000;
    static final int MAX_ID_LENGTH = 64;

    public EngineEventBatch {
        events = events == null ? null : List.copyOf(events);
    }

    /**
     * 单个事件信封。{@code occurredAt} 按发送端原样收为字符串（引擎写入的是 UTC 的
     * {@code Y-m-d H:i:s}，不带时区），由 {@link OccurredAtParser} 统一解释。
     */
    public record Envelope(
            @NotNull UUID eventId,
            @NotBlank @Size(max = MAX_ID_LENGTH) String eventType,
            @NotNull Integer schemaVersion,
            @NotBlank @Size(max = MAX_ID_LENGTH) String engineInstanceId,
            @NotNull @Positive Long surveyId,
            @NotBlank @Size(max = MAX_ID_LENGTH) String generation,
            @NotNull @Positive Long responseId,
            @NotBlank @Size(max = MAX_ID_LENGTH) String source,
            @NotBlank @Size(max = MAX_ID_LENGTH) String occurredAt) {
    }
}
