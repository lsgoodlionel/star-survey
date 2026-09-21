package cn.mjy.platform.engine.support;

import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;

import java.nio.charset.StandardCharsets;
import java.time.Instant;
import java.util.Arrays;
import java.util.List;
import java.util.Map;
import org.springframework.test.web.servlet.request.MockHttpServletRequestBuilder;

/**
 * 按 {@code MjyHttpEventTransport::send()} 的方式构造请求：
 * 请求体 {@code json_encode(['events' => $envelopes], JSON_UNESCAPED_UNICODE)}，
 * 头 {@code Content-Type: application/json}、{@code X-Mjy-Engine-Instance}（实例标识）、
 * {@code X-Mjy-Timestamp}（Unix 秒）、{@code X-Mjy-Signature}
 * （以该实例的派生密钥做 HMAC-SHA256，小写十六进制，覆盖 "时间戳.请求体"）。
 */
public final class SignedEventRequests {

    /** 平台主密钥（测试用）；引擎侧只持有由它派生出的实例密钥。 */
    public static final String MASTER_SECRET = "test-only-engine-events-secret-at-least-32-bytes";
    public static final String PATH = "/internal/engine-events";
    public static final String INSTANCE_HEADER = "X-Mjy-Engine-Instance";
    public static final String TIMESTAMP_HEADER = "X-Mjy-Timestamp";
    public static final String SIGNATURE_HEADER = "X-Mjy-Signature";
    /** 请求体里没有事件、无从推断实例时使用的实例标识。 */
    public static final String DEFAULT_INSTANCE = "engine-test";

    private SignedEventRequests() {
    }

    /** 某实例的派生密钥，即引擎侧配置的 MJY_PLATFORM_EVENTS_SECRET。 */
    public static String secretFor(String engineInstanceId) {
        return PhpEventSigner.deriveInstanceSecret(MASTER_SECRET, engineInstanceId);
    }

    public static byte[] body(PhpEnvelope... envelopes) {
        List<Map<String, Object>> events = Arrays.stream(envelopes).map(PhpEnvelope::toMap).toList();
        return PhpJson.encode(Map.of("events", events)).getBytes(StandardCharsets.UTF_8);
    }

    /** 以第一个事件的实例身份发送（与真实中继一致：一个引擎库只写自己的实例标识）。 */
    public static MockHttpServletRequestBuilder signed(PhpEnvelope... envelopes) {
        String instance = envelopes.length == 0 ? DEFAULT_INSTANCE : envelopes[0].instance();
        return signed(instance, body(envelopes));
    }

    public static MockHttpServletRequestBuilder signed(String engineInstanceId, byte[] body) {
        return signedAt(engineInstanceId, Instant.now().getEpochSecond(), body);
    }

    public static MockHttpServletRequestBuilder signedAt(String engineInstanceId, long epochSeconds, byte[] body) {
        String timestamp = Long.toString(epochSeconds);
        return raw(body, engineInstanceId, timestamp,
                PhpEventSigner.sign(secretFor(engineInstanceId), timestamp, body));
    }

    /** 逐个指定请求头；传 null 表示不带该头。 */
    public static MockHttpServletRequestBuilder raw(byte[] body, String engineInstanceId, String timestamp,
            String signature) {
        MockHttpServletRequestBuilder request = post(PATH).contentType("application/json").content(body);
        if (engineInstanceId != null) {
            request.header(INSTANCE_HEADER, engineInstanceId);
        }
        if (timestamp != null) {
            request.header(TIMESTAMP_HEADER, timestamp);
        }
        if (signature != null) {
            request.header(SIGNATURE_HEADER, signature);
        }
        return request;
    }
}
