package cn.mjy.platform.response;

import cn.mjy.platform.survey.gateway.PublishGatewaySigner;
import java.io.IOException;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.net.http.HttpTimeoutException;
import java.nio.charset.StandardCharsets;
import java.time.Clock;
import java.time.Duration;
import java.util.HashSet;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Component;
import tools.jackson.core.JacksonException;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.json.JsonMapper;
import tools.jackson.databind.node.ObjectNode;

/**
 * 契约 response-read-v1 的 HTTP 实现：{@code POST <网关>/v1/responses/read}。
 *
 * <p>与发布共用网关地址与共享密钥（{@code PLATFORM_PUBGW_URL} / {@code PLATFORM_PUBGW_SECRET}），签名规则相同。
 * 未配置时不阻止启动，但每次读取都失败（503），不会返回"没有作答"的假象。应答里出现未请求的答卷号即视为不可信。
 * 作答值是个人数据：本类只记录状态码与原因，从不记录请求或应答正文。
 */
@Component
public class HttpResponseAnswerSource implements ResponseAnswerSource {

    static final String READ_PATH = "/v1/responses/read";
    static final int MIN_SECRET_BYTES = 32;

    private static final Logger log = LoggerFactory.getLogger(HttpResponseAnswerSource.class);

    private final URI readUri;
    private final byte[] secret;
    private final Duration timeout;
    private final Clock clock;
    private final JsonMapper json;
    private final HttpClient http;

    @Autowired
    public HttpResponseAnswerSource(@Value("${platform.pubgw.url:}") String url,
            @Value("${platform.pubgw.secret:}") String secret,
            @Value("${platform.pubgw.read-timeout-seconds:30}") long timeoutSeconds,
            JsonMapper json) {
        this(parseBase(url), secret, Duration.ofSeconds(timeoutSeconds), Clock.systemUTC(), json);
    }

    HttpResponseAnswerSource(URI baseUrl, String secret, Duration timeout, Clock clock, JsonMapper json) {
        this.readUri = baseUrl == null ? null : baseUrl.resolve(READ_PATH);
        this.secret = secret == null ? new byte[0] : secret.getBytes(StandardCharsets.UTF_8);
        this.timeout = timeout;
        this.clock = clock;
        this.json = json;
        this.http = HttpClient.newBuilder().connectTimeout(timeout).build();
    }

    public boolean isConfigured() {
        return readUri != null && secret.length >= MIN_SECRET_BYTES;
    }

    @Override
    public AnswerBatch read(String engineInstanceId, long engineSid, List<Long> responseIds, List<String> fieldnames) {
        if (responseIds.isEmpty()) {
            return AnswerBatch.empty();
        }
        if (!isConfigured()) {
            throw new ResponseAnswersUnavailableException("publish gateway is not configured");
        }
        byte[] body = body(engineInstanceId, engineSid, responseIds, fieldnames);
        HttpResponse<byte[]> response = send(body);
        if (response.statusCode() != 200) {
            log.warn("response read on {} sid {} refused with http {}", engineInstanceId, engineSid,
                    response.statusCode());
            throw new ResponseAnswersUnavailableException("gateway returned http " + response.statusCode());
        }
        return parse(response.body(), Set.copyOf(responseIds), fieldnames);
    }

    private byte[] body(String engineInstanceId, long engineSid, List<Long> responseIds, List<String> fieldnames) {
        ObjectNode envelope = json.createObjectNode();
        envelope.put("engineInstanceId", engineInstanceId);
        envelope.put("surveyId", engineSid);
        responseIds.forEach(envelope.putArray("responseIds")::add);
        fieldnames.forEach(envelope.putArray("fields")::add);
        return json.writeValueAsBytes(envelope);
    }

    private HttpResponse<byte[]> send(byte[] body) {
        String timestamp = Long.toString(clock.instant().getEpochSecond());
        HttpRequest request = HttpRequest.newBuilder(readUri)
                .timeout(timeout)
                .header("Content-Type", "application/json")
                .header("X-Pubgw-Timestamp", timestamp)
                .header("X-Pubgw-Signature", PublishGatewaySigner.sign(secret, timestamp, body))
                .POST(HttpRequest.BodyPublishers.ofByteArray(body))
                .build();
        try {
            return http.send(request, HttpResponse.BodyHandlers.ofByteArray());
        } catch (HttpTimeoutException e) {
            throw new ResponseAnswersUnavailableException("gateway timed out after " + timeout);
        } catch (IOException e) {
            throw new ResponseAnswersUnavailableException("gateway network error: " + e.getClass().getSimpleName());
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            throw new ResponseAnswersUnavailableException("interrupted while reading answers");
        }
    }

    private AnswerBatch parse(byte[] body, Set<Long> requested, List<String> fieldnames) {
        try {
            JsonNode responses = json.readTree(body).get("responses");
            if (responses == null || !responses.isArray()) {
                throw untrusted("reply has no responses array");
            }
            Map<Long, Map<String, String>> answers = new LinkedHashMap<>();
            Set<Long> seen = new HashSet<>();
            for (JsonNode entry : responses) {
                long id = responseId(entry, requested, seen);
                answers.put(id, values(entry.get("values"), fieldnames));
            }
            return new AnswerBatch(answers);
        } catch (JacksonException e) {
            throw untrusted("reply is not JSON");
        }
    }

    private static long responseId(JsonNode entry, Set<Long> requested, Set<Long> seen) {
        JsonNode id = entry == null ? null : entry.get("id");
        if (id == null || !id.isIntegralNumber() || !requested.contains(id.asLong()) || !seen.add(id.asLong())) {
            throw untrusted("reply contains an unrequested or duplicate response id");
        }
        return id.asLong();
    }

    /** 只保留请求的列；缺的列当作未作答（null）。 */
    private static Map<String, String> values(JsonNode values, List<String> fieldnames) {
        if (values == null || !values.isObject()) {
            throw untrusted("reply entry has no values object");
        }
        Map<String, String> projected = new LinkedHashMap<>();
        for (String field : fieldnames) {
            JsonNode value = values.get(field);
            projected.put(field, value == null || value.isNull() ? null
                    : value.isString() ? value.asString() : value.toString());
        }
        return projected;
    }

    private static ResponseAnswersUnavailableException untrusted(String reason) {
        log.error("response read reply rejected: {}", reason);
        return new ResponseAnswersUnavailableException("untrusted gateway reply: " + reason);
    }

    private static URI parseBase(String url) {
        if (url == null || url.isBlank()) {
            return null;
        }
        try {
            URI uri = URI.create(url.trim());
            return uri.getScheme() != null && uri.getScheme().startsWith("http") ? uri : null;
        } catch (IllegalArgumentException e) {
            return null;
        }
    }
}
