package cn.mjy.platform.response;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import com.sun.net.httpserver.HttpServer;
import java.io.IOException;
import java.io.OutputStream;
import java.net.InetSocketAddress;
import java.net.URI;
import java.nio.charset.StandardCharsets;
import java.time.Clock;
import java.time.Duration;
import java.time.Instant;
import java.time.ZoneOffset;
import java.util.HexFormat;
import java.util.List;
import java.util.concurrent.atomic.AtomicReference;
import javax.crypto.Mac;
import javax.crypto.spec.SecretKeySpec;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.json.JsonMapper;

/** 按契约 platform/contracts/response-read-v1.md 调用网关读端点：签名、请求体、应答解析与失败即关闭。 */
class HttpResponseAnswerSourceTest {

    private static final String SECRET = "contract-test-secret-0123456789abcdef";
    private static final long NOW = 1_790_000_000L;

    private final JsonMapper json = JsonMapper.builder().build();
    private final AtomicReference<Captured> captured = new AtomicReference<>();
    private HttpServer server;
    private volatile int status;
    private volatile String reply;
    private volatile long delayMillis;

    private record Captured(String method, String path, String contentType, String timestamp, String signature,
            byte[] body) {
    }

    @BeforeEach
    void startGateway() throws IOException {
        server = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
        server.createContext("/", exchange -> {
            byte[] body = exchange.getRequestBody().readAllBytes();
            captured.set(new Captured(exchange.getRequestMethod(), exchange.getRequestURI().getPath(),
                    exchange.getRequestHeaders().getFirst("Content-Type"),
                    exchange.getRequestHeaders().getFirst("X-Pubgw-Timestamp"),
                    exchange.getRequestHeaders().getFirst("X-Pubgw-Signature"), body));
            sleep(delayMillis);
            byte[] out = reply.getBytes(StandardCharsets.UTF_8);
            exchange.sendResponseHeaders(status, out.length);
            try (OutputStream stream = exchange.getResponseBody()) {
                stream.write(out);
            }
        });
        server.start();
        status = 200;
        reply = """
                {"responses":[{"id":1,"values":{"9X1X1":"A1","9X1X2":null}},
                              {"id":3,"values":{"9X1X1":"A2","9X1X2":"你好","unrequested":"x"}}],
                 "missing":[2]}
                """;
    }

    @AfterEach
    void stopGateway() {
        server.stop(0);
    }

    private static void sleep(long millis) {
        try {
            Thread.sleep(millis);
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
        }
    }

    private HttpResponseAnswerSource client(String secret, Duration timeout) {
        URI base = URI.create("http://127.0.0.1:" + server.getAddress().getPort() + "/");
        Clock clock = Clock.fixed(Instant.ofEpochSecond(NOW), ZoneOffset.UTC);
        return new HttpResponseAnswerSource(base, secret, timeout, clock, json);
    }

    private AnswerBatch read() {
        return client(SECRET, Duration.ofSeconds(5)).read("hd-engine-01", 900001, List.of(1L, 2L, 3L),
                List.of("9X1X1", "9X1X2"));
    }

    @Test
    void theRequestIsSignedOverTheExactBytesSent() throws Exception {
        read();

        Captured request = captured.get();
        assertThat(request.method()).isEqualTo("POST");
        assertThat(request.path()).isEqualTo("/v1/responses/read");
        assertThat(request.contentType()).isEqualTo("application/json");
        assertThat(request.timestamp()).isEqualTo(Long.toString(NOW));
        Mac mac = Mac.getInstance("HmacSHA256");
        mac.init(new SecretKeySpec(SECRET.getBytes(StandardCharsets.UTF_8), "HmacSHA256"));
        mac.update((NOW + ".").getBytes(StandardCharsets.US_ASCII));
        assertThat(request.signature()).isEqualTo(HexFormat.of().formatHex(mac.doFinal(request.body())));
        JsonNode body = json.readTree(request.body());
        assertThat(body.get("engineInstanceId").asString()).isEqualTo("hd-engine-01");
        assertThat(body.get("surveyId").asLong()).isEqualTo(900001);
        assertThat(body.get("responseIds").toString()).isEqualTo("[1,2,3]");
        assertThat(body.get("fields").toString()).isEqualTo("[\"9X1X1\",\"9X1X2\"]");
    }

    @Test
    void answersAreKeyedByResponseIdAndProjectedToTheRequestedFields() {
        AnswerBatch batch = read();

        assertThat(batch.answers()).containsOnlyKeys(1L, 3L);
        assertThat(batch.answers().get(1L)).containsEntry("9X1X1", "A1").containsEntry("9X1X2", null);
        assertThat(batch.answers().get(3L)).containsEntry("9X1X2", "你好").doesNotContainKey("unrequested");
    }

    @Test
    void aResponseIdThatWasNotRequestedMeansTheReplyCannotBeTrusted() {
        reply = "{\"responses\":[{\"id\":99,\"values\":{}}],\"missing\":[]}";

        assertThatThrownBy(this::read).isInstanceOf(ResponseAnswersUnavailableException.class);
    }

    @Test
    void anyNon200IsUnavailable() {
        for (int code : new int[] {400, 401, 404, 500, 502}) {
            status = code;
            reply = "{\"error\":\"engine_error\"}";
            assertThatThrownBy(this::read).as("http " + code).isInstanceOf(ResponseAnswersUnavailableException.class);
        }
    }

    @Test
    void anUnreadableBodyIsUnavailable() {
        reply = "<html>login</html>";

        assertThatThrownBy(this::read).isInstanceOf(ResponseAnswersUnavailableException.class);
    }

    @Test
    void aTimeoutIsUnavailable() {
        delayMillis = 1500;

        assertThatThrownBy(() -> client(SECRET, Duration.ofMillis(300)).read("hd-engine-01", 1, List.of(1L),
                List.of("f"))).isInstanceOf(ResponseAnswersUnavailableException.class);
    }

    @Test
    void anUnconfiguredClientRefusesWithoutCallingTheGateway() {
        HttpResponseAnswerSource weak = client("too-short", Duration.ofSeconds(5));

        assertThat(weak.isConfigured()).isFalse();
        assertThatThrownBy(() -> weak.read("hd-engine-01", 1, List.of(1L), List.of("f")))
                .isInstanceOf(ResponseAnswersUnavailableException.class);
        assertThat(captured.get()).isNull();
    }

    @Test
    void anEmptyRequestNeverReachesTheGateway() {
        AnswerBatch batch = client(SECRET, Duration.ofSeconds(5)).read("hd-engine-01", 1, List.of(), List.of("f"));

        assertThat(batch.answers()).isEmpty();
        assertThat(captured.get()).isNull();
    }
}
