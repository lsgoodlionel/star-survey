package cn.mjy.platform.engine.support;

import java.util.LinkedHashMap;
import java.util.Map;
import java.util.UUID;

/**
 * 按 {@code MjyEventRelay::toEnvelope()} 的字段与顺序构造事件信封。
 * {@code occurredAt} 与引擎库读出的 datetime 一致：{@code gmdate('Y-m-d H:i:s')}，UTC、无时区后缀。
 */
public final class PhpEnvelope {

    public static final String SAVED = "response.saved";
    public static final String COMPLETED = "response.completed";
    public static final String DELETED = "response.deleted";

    private String eventId = UUID.randomUUID().toString();
    private String eventType = SAVED;
    private Object schemaVersion = 1;
    private String engineInstanceId = "engine-test";
    private Object surveyId = 880001;
    private String generation = UUID.randomUUID().toString();
    private Object responseId = 1;
    private String source = "hook";
    private String occurredAt = "2026-09-21 07:00:00";
    private final Map<String, Object> extra = new LinkedHashMap<>();

    public static PhpEnvelope event(String type) {
        PhpEnvelope envelope = new PhpEnvelope();
        envelope.eventType = type;
        return envelope;
    }

    public PhpEnvelope eventId(String value) {
        this.eventId = value;
        return this;
    }

    public PhpEnvelope instance(String value) {
        this.engineInstanceId = value;
        return this;
    }

    public PhpEnvelope survey(int value) {
        this.surveyId = value;
        return this;
    }

    public PhpEnvelope generation(String value) {
        this.generation = value;
        return this;
    }

    public PhpEnvelope response(int value) {
        this.responseId = value;
        return this;
    }

    public PhpEnvelope source(String value) {
        this.source = value;
        return this;
    }

    public PhpEnvelope occurredAt(String value) {
        this.occurredAt = value;
        return this;
    }

    public PhpEnvelope schemaVersion(int value) {
        this.schemaVersion = value;
        return this;
    }

    /** 额外字段：用来模拟发送端夹带的、平台必须忽略的内容（例如自称的租户）。 */
    public PhpEnvelope with(String key, Object value) {
        extra.put(key, value);
        return this;
    }

    public String eventId() {
        return eventId;
    }

    public Map<String, Object> toMap() {
        Map<String, Object> map = new LinkedHashMap<>();
        map.put("eventId", eventId);
        map.put("eventType", eventType);
        map.put("schemaVersion", schemaVersion);
        map.put("engineInstanceId", engineInstanceId);
        map.put("surveyId", surveyId);
        map.put("generation", generation);
        map.put("responseId", responseId);
        map.put("source", source);
        map.put("occurredAt", occurredAt);
        map.putAll(extra);
        return map;
    }
}
