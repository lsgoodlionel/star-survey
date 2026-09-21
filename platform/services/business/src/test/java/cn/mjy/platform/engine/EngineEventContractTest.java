package cn.mjy.platform.engine;

import static org.assertj.core.api.Assertions.assertThat;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

import cn.mjy.platform.engine.support.EngineEventsIntegrationTest;
import cn.mjy.platform.engine.support.EngineRows;
import cn.mjy.platform.engine.support.FakeEngineInstanceDirectory;
import cn.mjy.platform.engine.support.PhpEventSigner;
import cn.mjy.platform.engine.support.PhpJson;
import cn.mjy.platform.engine.support.SignedEventRequests;
import cn.mjy.platform.shared.TenantId;
import cn.mjy.platform.shared.tenant.TenantScope;
import java.nio.charset.StandardCharsets;
import java.time.Instant;
import java.time.OffsetDateTime;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.UUID;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.test.web.servlet.request.MockHttpServletRequestBuilder;

/**
 * 发送端与接收端的契约：逐步照抄 {@code plugins/MjyPlatformBridge} 里的 PHP 代码构造请求，
 * 断言平台接受它并正确落库。任何一端改了字段名、编码、签名串或请求头，这里都会失败。
 *
 * <pre>
 * MjyEventRelay::toEnvelope($row)   → 信封字段与顺序、整数化的 surveyId/responseId、schemaVersion = 1
 * MjyEventLog::insert()             → occurred_at = gmdate('Y-m-d H:i:s')（UTC，无时区）
 * MjyHttpEventTransport::send()     → $body = json_encode(['events' => $envelopes], JSON_UNESCAPED_UNICODE)
 *                                     $timestamp = (string) time()
 *                                     头：Content-Type: application/json / X-Mjy-Engine-Instance /
 *                                         X-Mjy-Timestamp / X-Mjy-Signature
 * MjyHttpEventTransport::sign()     → hash_hmac('sha256', $timestamp . '.' . $body, $secret)
 * MJY_PLATFORM_EVENTS_SECRET        → $secret = 运营签发的实例密钥
 *                                     = hex(HMAC-SHA256(主密钥, "mjy-engine-events/v1/" . 实例标识))
 * </pre>
 */
@EngineEventsIntegrationTest
class EngineEventContractTest {

    /** 含 "/"：PHP 会输出 {@code \/}；实例标识要放进请求头，只能是可见 ASCII。 */
    private static final String INSTANCE = "hd/engine-" + UUID.randomUUID();
    /** 含 "/" 与非 ASCII：PHP 会输出 {@code 华东\/relay}，任何按解析结果重算签名的实现都会失败。 */
    private static final String GENERATION = "gen-华东/relay";
    private static final String ENGINE_OCCURRED_AT = "2026-09-21 07:15:42";

    @Autowired
    private MockMvc mvc;

    @Autowired
    private FakeEngineInstanceDirectory directory;

    @Autowired
    private EngineRows rows;

    @Autowired
    private TenantScope scope;

    @Autowired
    private JdbcClient jdbc;

    private TenantId tenant;

    @BeforeEach
    void registerEngine() {
        tenant = TenantId.random();
        directory.register(INSTANCE, tenant);
    }

    @Test
    void theEndpointAcceptsARequestBuiltExactlyLikeMjyHttpEventTransport() throws Exception {
        List<Map<String, Object>> envelopes = List.of(
                toEnvelope(eventLogRow("response.saved", 101, "hook")),
                toEnvelope(eventLogRow("response.completed", 101, "scanner")));
        byte[] body = phpJsonEncodeEvents(envelopes);

        mvc.perform(phpSend(body))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.received").value(2))
                .andExpect(jsonPath("$.accepted").value(2));

        assertThat(new String(body, StandardCharsets.UTF_8)).contains("gen-华东\\/relay").contains("hd\\/engine-");
        assertThat(rows.states(tenant, INSTANCE, 880001, GENERATION, 101)).containsExactly("engine_completed");
        assertThat(rows.outboxTypes(tenant)).containsExactly("response.engine_completed");
    }

    @Test
    void theEngineTimestampIsReadAsUtcAndEveryEnvelopeFieldLands() throws Exception {
        Map<String, Object> envelope = toEnvelope(eventLogRow("response.completed", 102, "scanner"));

        mvc.perform(phpSend(phpJsonEncodeEvents(List.of(envelope)))).andExpect(status().isOk());

        Map<String, Object> stored = scope.call(tenant, () -> jdbc.sql("""
                SELECT event_id::text AS event_id, event_type, schema_version, engine_instance_id, survey_id,
                       generation, response_id, source, occurred_at
                FROM engine_event_inbox
                """).query().singleRow());
        assertThat(stored).containsEntry("event_id", envelope.get("eventId"))
                .containsEntry("event_type", "response.completed")
                .containsEntry("schema_version", 1)
                .containsEntry("engine_instance_id", INSTANCE)
                .containsEntry("survey_id", 880001L)
                .containsEntry("generation", GENERATION)
                .containsEntry("response_id", 102L)
                .containsEntry("source", "scanner");
        Instant occurredAt = scope.call(tenant, () -> jdbc.sql("SELECT occurred_at FROM engine_event_inbox")
                .query(OffsetDateTime.class).single()).toInstant();
        assertThat(occurredAt).isEqualTo(Instant.parse("2026-09-21T07:15:42Z"));
    }

    @Test
    void aRetriedSendIsReSignedWithAFreshTimestampAndAcknowledgedAsDuplicates() throws Exception {
        // 中继失败后下一轮 cron 重发同一批行：请求体相同，时间戳与签名是新的。
        byte[] body = phpJsonEncodeEvents(List.of(toEnvelope(eventLogRow("response.completed", 103, "hook"))));
        long firstSend = Instant.now().getEpochSecond() - 120;
        mvc.perform(phpSendAt(body, firstSend)).andExpect(status().isOk());

        mvc.perform(phpSendAt(body, firstSend + 60))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.duplicates").value(1));

        assertThat(rows.outbox(tenant)).isEqualTo(1);
    }

    @Test
    void aSignatureComputedOverReSerializedJsonIsRejected() throws Exception {
        // 反例：若有人按"解析后再序列化"的字节签名（Java 默认不转义 /），签名对不上。
        byte[] phpBody = phpJsonEncodeEvents(List.of(toEnvelope(eventLogRow("response.saved", 104, "hook"))));
        byte[] reSerialized = new String(phpBody, StandardCharsets.UTF_8).replace("\\/", "/")
                .getBytes(StandardCharsets.UTF_8);
        String timestamp = Long.toString(Instant.now().getEpochSecond());

        mvc.perform(SignedEventRequests.raw(phpBody, INSTANCE, timestamp,
                        PhpEventSigner.sign(SignedEventRequests.secretFor(INSTANCE), timestamp, reSerialized)))
                .andExpect(status().isUnauthorized());
        assertThat(rows.inbox(tenant)).isZero();
    }

    /** 引擎事件表的一行，形如 {@code SELECT * FROM lime_mjyplatformbridge_event_log}（PDO 返回的全是字符串）。 */
    private static Map<String, String> eventLogRow(String type, int responseId, String source) {
        Map<String, String> row = new LinkedHashMap<>();
        row.put("id", "1");
        row.put("event_id", UUID.randomUUID().toString());
        row.put("event_type", type);
        row.put("engine_instance_id", INSTANCE);
        row.put("survey_id", "880001");
        row.put("generation", GENERATION);
        row.put("response_id", Integer.toString(responseId));
        row.put("source", source);
        row.put("dedupe_key", null);
        row.put("occurred_at", ENGINE_OCCURRED_AT);
        row.put("delivered_at", null);
        return row;
    }

    /** 照抄 {@code MjyEventRelay::toEnvelope()}：字段顺序、(int) 转换、SCHEMA_VERSION。 */
    private static Map<String, Object> toEnvelope(Map<String, String> row) {
        Map<String, Object> envelope = new LinkedHashMap<>();
        envelope.put("eventId", row.get("event_id"));
        envelope.put("eventType", row.get("event_type"));
        envelope.put("schemaVersion", 1);
        envelope.put("engineInstanceId", row.get("engine_instance_id"));
        envelope.put("surveyId", Integer.parseInt(row.get("survey_id")));
        envelope.put("generation", row.get("generation"));
        envelope.put("responseId", Integer.parseInt(row.get("response_id")));
        envelope.put("source", row.get("source"));
        envelope.put("occurredAt", row.get("occurred_at"));
        return envelope;
    }

    /** 照抄 {@code json_encode(['events' => $envelopes], JSON_UNESCAPED_UNICODE)}。 */
    private static byte[] phpJsonEncodeEvents(List<Map<String, Object>> envelopes) {
        return PhpJson.encode(Map.of("events", envelopes)).getBytes(StandardCharsets.UTF_8);
    }

    private static MockHttpServletRequestBuilder phpSend(byte[] body) {
        return phpSendAt(body, Instant.now().getEpochSecond());
    }

    /** 照抄 {@code MjyHttpEventTransport::send()} 的请求行、请求头与签名（用本实例的派生密钥）。 */
    private static MockHttpServletRequestBuilder phpSendAt(byte[] body, long time) {
        String timestamp = Long.toString(time);
        String signature = PhpEventSigner.sign(SignedEventRequests.secretFor(INSTANCE), timestamp, body);
        return post("/internal/engine-events")
                .header("Content-Type", "application/json")
                .header("X-Mjy-Engine-Instance", INSTANCE)
                .header("X-Mjy-Timestamp", timestamp)
                .header("X-Mjy-Signature", signature)
                .content(body);
    }
}
