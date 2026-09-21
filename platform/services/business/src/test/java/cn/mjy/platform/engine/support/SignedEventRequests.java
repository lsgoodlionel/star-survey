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
 * 头 {@code Content-Type: application/json}、{@code X-Mjy-Timestamp}（Unix 秒）、
 * {@code X-Mjy-Signature}（HMAC-SHA256 小写十六进制，覆盖 "时间戳.请求体"）。
 */
public final class SignedEventRequests {

    public static final String SECRET = "test-only-engine-events-secret-at-least-32-bytes";
    public static final String PATH = "/internal/engine-events";

    private SignedEventRequests() {
    }

    public static byte[] body(PhpEnvelope... envelopes) {
        List<Map<String, Object>> events = Arrays.stream(envelopes).map(PhpEnvelope::toMap).toList();
        return PhpJson.encode(Map.of("events", events)).getBytes(StandardCharsets.UTF_8);
    }

    public static MockHttpServletRequestBuilder signed(PhpEnvelope... envelopes) {
        return signed(body(envelopes));
    }

    public static MockHttpServletRequestBuilder signed(byte[] body) {
        return signedAt(Instant.now().getEpochSecond(), body);
    }

    public static MockHttpServletRequestBuilder signedAt(long epochSeconds, byte[] body) {
        String timestamp = Long.toString(epochSeconds);
        return raw(body, timestamp, PhpEventSigner.sign(SECRET, timestamp, body));
    }

    public static MockHttpServletRequestBuilder raw(byte[] body, String timestamp, String signature) {
        MockHttpServletRequestBuilder request = post(PATH).contentType("application/json").content(body);
        if (timestamp != null) {
            request.header("X-Mjy-Timestamp", timestamp);
        }
        if (signature != null) {
            request.header("X-Mjy-Signature", signature);
        }
        return request;
    }
}
