package cn.mjy.platform.asset;

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
import java.util.Base64;
import java.util.HexFormat;
import java.util.List;
import java.util.UUID;
import java.util.concurrent.atomic.AtomicReference;
import javax.crypto.Mac;
import javax.crypto.spec.SecretKeySpec;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.json.JsonMapper;

/**
 * 平台 → 网关的 {@code POST /v1/responses/uploads}（ADR 0019 决定 8）。
 *
 * <p>与 {@code HttpResponseAnswerSource} 同一套签名与同一条自律：网关的应答是<b>外部数据</b>，
 * 形状不对就拒绝，绝不猜；读不到一律抛，绝不返回"这份答卷没有上传"的假象。
 */
class HttpResponderUploadSourceTest {

    private static final String SECRET = "contract-test-secret-0123456789abcdef";
    private static final long NOW = 1_790_000_000L;
    private static final String INSTANCE = "engine-a";
    private static final long SID = 4242L;
    private static final String GENERATION = "gen-1";
    private static final UUID TOKEN = UUID.fromString("11111111-2222-4333-8444-555555555555");

    private final JsonMapper json = JsonMapper.builder().build();
    private final AtomicReference<Captured> captured = new AtomicReference<>();
    private HttpServer server;
    private volatile int status;
    private volatile String reply;

    private record Captured(String method, String path, String timestamp, String signature, byte[] body) {
    }

    @BeforeEach
    void startGateway() throws IOException {
        server = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
        server.createContext("/", exchange -> {
            byte[] body = exchange.getRequestBody().readAllBytes();
            captured.set(new Captured(exchange.getRequestMethod(), exchange.getRequestURI().getPath(),
                    exchange.getRequestHeaders().getFirst("X-Pubgw-Timestamp"),
                    exchange.getRequestHeaders().getFirst("X-Pubgw-Signature"), body));
            byte[] out = reply.getBytes(StandardCharsets.UTF_8);
            exchange.sendResponseHeaders(status, out.length);
            try (OutputStream stream = exchange.getResponseBody()) {
                stream.write(out);
            }
        });
        server.start();
        status = 200;
        reply = "{\"uploads\":{}}";
    }

    @AfterEach
    void stopGateway() {
        server.stop(0);
    }

    private HttpResponderUploadSource source() {
        URI base = URI.create("http://127.0.0.1:" + server.getAddress().getPort());
        return new HttpResponderUploadSource(base, SECRET, Duration.ofSeconds(5),
                Clock.fixed(Instant.ofEpochSecond(NOW), ZoneOffset.UTC), json);
    }

    @Test
    void theManifestRequestIsSignedAndNamesTheGeneration() {
        source().manifest(INSTANCE, SID, GENERATION, List.of(7L, 9L));

        Captured request = captured.get();
        assertThat(request.method()).isEqualTo("POST");
        assertThat(request.path()).isEqualTo("/v1/responses/uploads");
        assertThat(request.timestamp()).isEqualTo(Long.toString(NOW));
        assertThat(request.signature()).isEqualTo(expectedSignature(request.timestamp(), request.body()));
        JsonNode body = json.readTree(request.body());
        assertThat(body.get("engineInstanceId").asString()).isEqualTo(INSTANCE);
        assertThat(body.get("generation").asString()).isEqualTo(GENERATION);
        assertThat(body.get("responseIds").size()).isEqualTo(2);
        // 平台从不按文件名要文件：契约里根本没有那个字段。
        assertThat(body.has("fileNames")).isFalse();
    }

    @Test
    void aManifestIsParsedIntoRefs() {
        reply = """
                {"uploads":{"7":[{"uploadToken":"11111111-2222-4333-8444-555555555555",
                                  "questionCode":"REC1","originalName":"录音.webm",
                                  "extension":"webm","sizeBytes":12}]}}
                """;

        List<ResponderUploadRef> refs = source().manifest(INSTANCE, SID, GENERATION, List.of(7L));

        assertThat(refs).singleElement().satisfies(ref -> {
            assertThat(ref.uploadToken()).isEqualTo(TOKEN);
            assertThat(ref.responseId()).isEqualTo(7L);
            assertThat(ref.questionCode()).isEqualTo("REC1");
            assertThat(ref.originalName()).isEqualTo("录音.webm");
        });
    }

    /** 应答里出现没请求过的答卷号即视为不可信（与作答读取同一条口径）。 */
    @Test
    void aManifestNamingAnUnrequestedResponseIsRefused() {
        reply = """
                {"uploads":{"99":[{"uploadToken":"11111111-2222-4333-8444-555555555555",
                                   "questionCode":"REC1","originalName":"a","extension":"webm","sizeBytes":1}]}}
                """;

        assertThatThrownBy(() -> source().manifest(INSTANCE, SID, GENERATION, List.of(7L)))
                .isInstanceOf(ResponderUploadsUnavailableException.class);
    }

    @Test
    void contentIsDecodedFromBase64() {
        byte[] raw = AssetFixture.webm();
        reply = "{\"uploadToken\":\"" + TOKEN + "\",\"questionCode\":\"REC1\",\"responseId\":7,"
                + "\"originalName\":\"a.webm\",\"extension\":\"webm\",\"sizeBytes\":" + raw.length
                + ",\"contentBase64\":\"" + Base64.getEncoder().encodeToString(raw) + "\"}";

        ResponderUploadBytes content = source().content(INSTANCE, SID, GENERATION, TOKEN);

        assertThat(content.content()).isEqualTo(raw);
        assertThat(content.ref().uploadToken()).isEqualTo(TOKEN);
        assertThat(content.ref().responseId()).isEqualTo(7L);
    }

    /** 网关回的是另一件上传：绝不能当成这一件存下去。 */
    @Test
    void contentAboutAnotherTokenIsRefused() {
        reply = "{\"uploadToken\":\"99999999-2222-4333-8444-555555555555\",\"questionCode\":\"REC1\","
                + "\"responseId\":7,\"originalName\":\"a\",\"extension\":\"webm\",\"sizeBytes\":1,"
                + "\"contentBase64\":\"AA==\"}";

        assertThatThrownBy(() -> source().content(INSTANCE, SID, GENERATION, TOKEN))
                .isInstanceOf(ResponderUploadsUnavailableException.class);
    }

    @Test
    void contentWhoseDeclaredSizeDisagreesWithItsBytesIsRefused() {
        reply = "{\"uploadToken\":\"" + TOKEN + "\",\"questionCode\":\"REC1\",\"responseId\":7,"
                + "\"originalName\":\"a\",\"extension\":\"webm\",\"sizeBytes\":99,\"contentBase64\":\"AA==\"}";

        assertThatThrownBy(() -> source().content(INSTANCE, SID, GENERATION, TOKEN))
                .isInstanceOf(ResponderUploadsUnavailableException.class);
    }

    /** 网关失败一律关闭：绝不返回"这份答卷没有上传"的假象。 */
    @Test
    void agatewayErrorIsNeverReadAsNoUploads() {
        status = 502;
        reply = "{\"error\":\"engine_error\"}";

        assertThatThrownBy(() -> source().manifest(INSTANCE, SID, GENERATION, List.of(7L)))
                .isInstanceOf(ResponderUploadsUnavailableException.class);
    }

    @Test
    void areplyThatIsNotJsonIsRefused() {
        reply = "<html>oops</html>";

        assertThatThrownBy(() -> source().manifest(INSTANCE, SID, GENERATION, List.of(7L)))
                .isInstanceOf(ResponderUploadsUnavailableException.class);
    }

    /** 没配网关地址或密钥：每次调用都失败，不会装作"没有上传"。 */
    @Test
    void anUnconfiguredSourceFailsClosed() {
        HttpResponderUploadSource unconfigured = new HttpResponderUploadSource(null, "", Duration.ofSeconds(5),
                Clock.systemUTC(), json);

        assertThat(unconfigured.isConfigured()).isFalse();
        assertThatThrownBy(() -> unconfigured.manifest(INSTANCE, SID, GENERATION, List.of(7L)))
                .isInstanceOf(ResponderUploadsUnavailableException.class);
    }

    /**
     * 应答体量必须在<b>解析之前</b>就封顶（独立安全审查的 MEDIUM）。
     *
     * <p>原先先把整个应答读成字节数组再 {@code readTree}，只有之后才看 {@code sizeBytes}——
     * 一个被攻破的插件返回一坨超大的 {@code contentBase64}，平台就得先把它整个读进内存、
     * 再整棵树解析完，才走到那个上限判断。判断放得太晚等于没有判断。
     */
    @Test
    void agatewayReplyLargerThanTheCapIsRefusedWithoutBeingParsed() {
        reply = "{\"uploadToken\":\"" + TOKEN + "\",\"questionCode\":\"REC1\",\"responseId\":7,"
                + "\"originalName\":\"a\",\"extension\":\"webm\",\"sizeBytes\":1,\"contentBase64\":\""
                + "A".repeat((int) (HttpResponderUploadSource.MAX_RESPONSE_BYTES + 1024)) + "\"}";

        assertThatThrownBy(() -> source().content(INSTANCE, SID, GENERATION, TOKEN))
                .isInstanceOf(ResponderUploadsUnavailableException.class)
                .hasMessageContaining("too large");
    }

    @Test
    void anEmptyPageNeverLeavesTheProcess() {
        assertThat(source().manifest(INSTANCE, SID, GENERATION, List.of())).isEmpty();
        assertThat(captured.get()).isNull();
    }

    private static String expectedSignature(String timestamp, byte[] body) {
        try {
            Mac mac = Mac.getInstance("HmacSHA256");
            mac.init(new SecretKeySpec(SECRET.getBytes(StandardCharsets.UTF_8), "HmacSHA256"));
            mac.update(timestamp.getBytes(StandardCharsets.UTF_8));
            mac.update((byte) '.');
            mac.update(body);
            return HexFormat.of().formatHex(mac.doFinal());
        } catch (Exception e) {
            throw new IllegalStateException(e);
        }
    }
}
