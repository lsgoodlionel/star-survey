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
import java.util.List;
import java.util.Map;
import java.util.concurrent.atomic.AtomicReference;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.json.JsonMapper;

/**
 * 读端点的 {@code includeRespondent}（契约 response-read-v1「参与者令牌」，ADR 0016 缺口 (b)）。
 *
 * <p>对账只要"这份答卷是谁交的"，所以请求空的 fields、只取令牌。匿名问卷网关一律回 null，
 * 平台照此把这份答卷当作"认不出是谁"，而不是当作某个人答的。
 */
class HttpRespondentReadTest {

    private static final String SECRET = "contract-test-secret-0123456789abcdef";
    private static final long NOW = 1_790_000_000L;

    private final JsonMapper json = JsonMapper.builder().build();
    private final AtomicReference<byte[]> captured = new AtomicReference<>();
    private HttpServer server;
    private volatile int status;
    private volatile String reply;

    @BeforeEach
    void startGateway() throws IOException {
        server = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
        server.createContext("/", exchange -> {
            captured.set(exchange.getRequestBody().readAllBytes());
            byte[] out = reply.getBytes(StandardCharsets.UTF_8);
            exchange.sendResponseHeaders(status, out.length);
            try (OutputStream stream = exchange.getResponseBody()) {
                stream.write(out);
            }
        });
        server.start();
        status = 200;
        reply = """
                {"responses":[{"id":1,"values":{},"token":"tok-zhang"},
                              {"id":3,"values":{},"token":null}],
                 "missing":[2]}
                """;
    }

    @AfterEach
    void stopGateway() {
        server.stop(0);
    }

    private HttpResponseAnswerSource client() {
        URI base = URI.create("http://127.0.0.1:" + server.getAddress().getPort() + "/");
        Clock clock = Clock.fixed(Instant.ofEpochSecond(NOW), ZoneOffset.UTC);
        return new HttpResponseAnswerSource(base, SECRET, Duration.ofSeconds(5), clock, json);
    }

    private Map<Long, String> read() {
        return client().readRespondents("hd-engine-01", 900001, List.of(1L, 2L, 3L));
    }

    @Test
    void itAsksForTheRespondentAndForNoAnswerColumns() throws Exception {
        read();

        JsonNode body = json.readTree(captured.get());
        assertThat(body.get("includeRespondent").asBoolean()).isTrue();
        assertThat(body.get("fields").toString()).isEqualTo("[]");
        assertThat(body.get("responseIds").toString()).isEqualTo("[1,2,3]");
    }

    @Test
    void itReportsTheTokenEachResponseWasAnsweredWith() {
        assertThat(read()).containsExactly(Map.entry(1L, "tok-zhang"));
    }

    @Test
    void aResponseWithoutATokenIsLeftOutRatherThanMappedToNothing() {
        // 匿名问卷、或没用邀请码进场的答卷：认不出是谁，不能当作某个人答的。
        assertThat(read()).doesNotContainKey(3L);
    }

    @Test
    void anEmptyRequestNeverReachesTheGateway() {
        assertThat(client().readRespondents("hd-engine-01", 900001, List.of())).isEmpty();
        assertThat(captured.get()).isNull();
    }

    @Test
    void anUnrequestedResponseIdMakesTheWholeReplyUntrusted() {
        reply = """
                {"responses":[{"id":77,"values":{},"token":"tok"}],"missing":[]}
                """;

        assertThatThrownBy(this::read).isInstanceOf(ResponseAnswersUnavailableException.class);
    }

    @Test
    void aGatewayRefusalIsNotReadAsNobodyAnswered() {
        status = 502;
        reply = "{\"error\":\"engine_error\"}";

        assertThatThrownBy(this::read).isInstanceOf(ResponseAnswersUnavailableException.class);
    }
}
