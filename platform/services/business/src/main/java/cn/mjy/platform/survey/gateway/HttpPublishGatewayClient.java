package cn.mjy.platform.survey.gateway;

import java.io.IOException;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.net.http.HttpTimeoutException;
import java.nio.charset.StandardCharsets;
import java.time.Clock;
import java.time.Duration;
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
 * 契约 publish-gateway-v1（及 v1.2 增补）的 HTTP 实现。
 *
 * <p>配置只来自环境变量：{@code PLATFORM_PUBGW_URL}（网关基础地址）、{@code PLATFORM_PUBGW_SECRET}
 * （共享密钥，至少 {@value #MIN_SECRET_BYTES} 字节，与网关侧 {@code PUBGW_SHARED_SECRET} 相同）。
 * 缺失或过短时不阻止应用启动（其他模块照常服务），但 {@link #isConfigured()} 为假，发布被拒绝——失败即关闭。
 * 密钥不进日志。
 *
 * <p>超时要大于网关跑完七个阶段所需的时间；超时本身不代表失败，只代表结果未知。
 */
@Component
public class HttpPublishGatewayClient implements PublishGatewayClient {

    static final int MIN_SECRET_BYTES = 32;
    static final String PUBLISH_PATH = "/v1/publish";
    static final String CLOSE_PATH = "/v1/close";
    static final String DRIFT_CHECK_PATH = "/v1/drift-check";
    static final String TIMESTAMP_HEADER = "X-Pubgw-Timestamp";
    static final String SIGNATURE_HEADER = "X-Pubgw-Signature";

    private static final Logger log = LoggerFactory.getLogger(HttpPublishGatewayClient.class);

    private final URI baseUrl;
    private final byte[] secret;
    private final Duration timeout;
    private final Clock clock;
    private final JsonMapper json;
    private final HttpClient http;

    /** 一次 HTTP 往返的原始结果；失败时 status 为 0、failure 说明原因。 */
    private record Exchange(int status, byte[] body, String failure) {
    }

    @Autowired
    public HttpPublishGatewayClient(@Value("${platform.pubgw.url:}") String url,
            @Value("${platform.pubgw.secret:}") String secret,
            @Value("${platform.pubgw.timeout-seconds:120}") long timeoutSeconds,
            JsonMapper json) {
        this(parseBase(url), secret, Duration.ofSeconds(timeoutSeconds), Clock.systemUTC(), json);
        if (!isConfigured()) {
            log.warn("publish gateway is not configured (PLATFORM_PUBGW_URL / PLATFORM_PUBGW_SECRET >= {} bytes); "
                    + "survey publishing will be refused", MIN_SECRET_BYTES);
        }
    }

    HttpPublishGatewayClient(URI baseUrl, String secret, Duration timeout, Clock clock, JsonMapper json) {
        this.baseUrl = baseUrl;
        this.secret = secret == null ? new byte[0] : secret.getBytes(StandardCharsets.UTF_8);
        this.timeout = timeout;
        this.clock = clock;
        this.json = json;
        this.http = HttpClient.newBuilder().connectTimeout(timeout).build();
    }

    @Override
    public boolean isConfigured() {
        return baseUrl != null && secret.length >= MIN_SECRET_BYTES;
    }

    @Override
    public GatewayOutcome publish(GatewayRequest request) {
        if (!isConfigured()) {
            return new GatewayOutcome.Refused(0, "gateway_not_configured");
        }
        ObjectNode envelope = json.createObjectNode();
        envelope.put("requestId", request.requestId().toString());
        envelope.put("engineInstanceId", request.engineInstanceId());
        envelope.set("definition", json.readTree(request.definitionJson()));
        Exchange exchange = post(PUBLISH_PATH, json.writeValueAsBytes(envelope));
        if (exchange.failure() != null) {
            log.warn("publish gateway outcome unknown for request {}: {}", request.requestId(), exchange.failure());
            return new GatewayOutcome.Unknown(exchange.failure());
        }
        return classify(exchange.status(), exchange.body());
    }

    @Override
    public CloseOutcome close(GatewayCloseRequest request) {
        if (!isConfigured()) {
            return new CloseOutcome.NotClosed("gateway_not_configured");
        }
        ObjectNode envelope = json.createObjectNode();
        envelope.put("requestId", request.requestId().toString());
        envelope.put("engineInstanceId", request.engineInstanceId());
        envelope.put("surveyId", request.surveyId());
        Exchange exchange = post(CLOSE_PATH, json.writeValueAsBytes(envelope));
        if (exchange.failure() != null) {
            return new CloseOutcome.NotClosed(exchange.failure());
        }
        try {
            JsonNode body = readBody(exchange);
            if (exchange.status() == 200) {
                return GatewayOperationsParser.closed(body, request.surveyId());
            }
            return new CloseOutcome.NotClosed("http " + exchange.status() + " " + errorOf(body));
        } catch (JacksonException | GatewayResponseParser.MalformedResponseException e) {
            log.error("publish gateway returned an unreadable close response (http {}): {}", exchange.status(),
                    e.getMessage());
            return new CloseOutcome.NotClosed("unreadable http " + exchange.status() + " response");
        }
    }

    @Override
    public DriftOutcome driftCheck(GatewayDriftRequest request) {
        if (!isConfigured()) {
            return new DriftOutcome.Unavailable("gateway_not_configured");
        }
        ObjectNode envelope = json.createObjectNode();
        envelope.put("engineInstanceId", request.engineInstanceId());
        envelope.put("surveyId", request.surveyId());
        envelope.put("expectedFingerprint", request.expectedFingerprint());
        if (request.bindingJson() != null) {
            envelope.set("binding", json.readTree(request.bindingJson()));
        }
        Exchange exchange = post(DRIFT_CHECK_PATH, json.writeValueAsBytes(envelope));
        if (exchange.failure() != null) {
            return new DriftOutcome.Unavailable(exchange.failure());
        }
        try {
            JsonNode body = readBody(exchange);
            if (exchange.status() == 200) {
                return GatewayOperationsParser.checked(body, request.surveyId());
            }
            return new DriftOutcome.Unavailable("http " + exchange.status() + " " + errorOf(body));
        } catch (JacksonException | GatewayResponseParser.MalformedResponseException e) {
            log.error("publish gateway returned an unreadable drift-check response (http {}): {}", exchange.status(),
                    e.getMessage());
            return new DriftOutcome.Unavailable("unreadable http " + exchange.status() + " response");
        }
    }

    /** 签名并发送；网络层的一切失败都折成 {@link Exchange#failure()}，绝不抛出。 */
    private Exchange post(String path, byte[] body) {
        String timestamp = Long.toString(clock.instant().getEpochSecond());
        HttpRequest httpRequest = HttpRequest.newBuilder(baseUrl.resolve(path))
                .timeout(timeout)
                .header("Content-Type", "application/json")
                .header(TIMESTAMP_HEADER, timestamp)
                .header(SIGNATURE_HEADER, PublishGatewaySigner.sign(secret, timestamp, body))
                .POST(HttpRequest.BodyPublishers.ofByteArray(body))
                .build();
        try {
            HttpResponse<byte[]> response = http.send(httpRequest, HttpResponse.BodyHandlers.ofByteArray());
            return new Exchange(response.statusCode(), response.body(), null);
        } catch (HttpTimeoutException e) {
            return new Exchange(0, new byte[0], "timed out after " + timeout);
        } catch (IOException e) {
            return new Exchange(0, new byte[0], "network error: " + e.getClass().getSimpleName());
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            return new Exchange(0, new byte[0], "interrupted");
        }
    }

    private JsonNode readBody(Exchange exchange) {
        return exchange.body().length == 0 ? null : json.readTree(exchange.body());
    }

    private static String errorOf(JsonNode body) {
        JsonNode error = body == null ? null : body.get("error");
        return error != null && error.isString() ? error.asString() : "";
    }

    private GatewayOutcome classify(int status, byte[] responseBody) {
        try {
            JsonNode body = responseBody.length == 0 ? null : json.readTree(responseBody);
            return switch (status) {
                case 200 -> new GatewayOutcome.Published(GatewayResponseParser.result(body, true));
                case 422, 502 -> new GatewayOutcome.Failed(status, GatewayResponseParser.result(body, false));
                case 400, 401, 404 -> new GatewayOutcome.Refused(status, GatewayResponseParser.error(body, status));
                case 410 -> GatewayResponseParser.expired(body);
                default -> new GatewayOutcome.Unknown("gateway returned http " + status);
            };
        } catch (JacksonException | GatewayResponseParser.MalformedResponseException e) {
            log.error("publish gateway returned an unreadable http {} response: {}", status, e.getMessage());
            return new GatewayOutcome.Unknown("unreadable http " + status + " response");
        }
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
