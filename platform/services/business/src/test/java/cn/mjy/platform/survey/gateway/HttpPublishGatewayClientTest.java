package cn.mjy.platform.survey.gateway;

import static org.assertj.core.api.Assertions.assertThat;

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
import java.util.UUID;
import java.util.concurrent.atomic.AtomicReference;
import javax.crypto.Mac;
import javax.crypto.spec.SecretKeySpec;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.json.JsonMapper;

/** 按契约 platform/contracts/publish-gateway-v1.md 调用网关：签名逐字节一致，应答按状态码归类。 */
class HttpPublishGatewayClientTest {

    private static final String SECRET = "contract-test-secret-0123456789abcdef";
    private static final long NOW = 1_790_000_000L;
    private static final UUID REQUEST_ID = UUID.fromString("0b0d3f2e-0000-4000-8000-000000000001");
    private static final String DEFINITION = "{\"uuid\":\"11111111-1111-4111-8111-111111111111\",\"title\":\"满意度 / survey\"}";
    private static final String BINDING = """
            {"engineInstance":"hd-engine-01","surveyId":900001,
             "definitionUuid":"11111111-1111-4111-8111-111111111111","compilerVersion":"pubgw-lss-1",
             "fingerprintVersion":"fm1","fingerprint":"fm1:74e0d199d9839cdc","language":"en",
             "publishedAt":"2026-09-20T08:30:54Z",
             "questions":[{"uuid":"33333333-0001-4111-8111-000000000001","code":"QSINGLE","type":"L",
               "fields":[{"fieldname":"Q256","aid":"","scale":0},{"fieldname":"Q256_Cother","aid":"other","scale":0}]}]}
            """;

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
            exchange.getResponseHeaders().add("Content-Type", "application/json");
            exchange.sendResponseHeaders(status, out.length);
            try (OutputStream stream = exchange.getResponseBody()) {
                stream.write(out);
            }
        });
        server.start();
        status = 200;
        reply = "{\"status\":\"published\",\"result\":" + result(true, "null", "[]", "null", BINDING) + "}";
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

    private static String result(boolean ok, String failedStage, String failures, String orphan, String binding) {
        return """
                {"ok":%s,"surveyId":%s,"failedStage":%s,"failures":%s,"rolledBack":%s,"orphanSurveyId":%s,
                 "steps":[],"binding":%s,"verification":null}
                """.formatted(ok, ok ? "900001" : "null", failedStage, failures, orphan.equals("null"), orphan,
                binding);
    }

    private HttpPublishGatewayClient client(Duration timeout) {
        URI base = URI.create("http://127.0.0.1:" + server.getAddress().getPort());
        Clock clock = Clock.fixed(Instant.ofEpochSecond(NOW), ZoneOffset.UTC);
        return new HttpPublishGatewayClient(base, SECRET, timeout, clock, json);
    }

    private GatewayOutcome publish() {
        return client(Duration.ofSeconds(5)).publish(new GatewayRequest(REQUEST_ID, "hd-engine-01", DEFINITION));
    }

    @Test
    void theSignatureIsHexHmacSha256OfTimestampDotRawBody() {
        byte[] body = ("{\"requestId\":\"0b0d3f2e-0000-4000-8000-000000000001\",\"engineInstanceId\":\"hd-engine-01\","
                + "\"definition\":{\"title\":\"满意度 / survey\"}}").getBytes(StandardCharsets.UTF_8);

        String signature = PublishGatewaySigner.sign(SECRET.getBytes(StandardCharsets.UTF_8), "1790000000", body);

        // 期望值由 Python hmac.new(secret, b"1790000000." + body, sha256).hexdigest() 独立算出。
        assertThat(signature).isEqualTo("e421fffe520d9a075843e1b4a12a0823db317baa482ea209f16d522ac432d16e");
    }

    @Test
    void theRequestIsSignedOverTheExactBytesThatAreSent() throws Exception {
        publish();

        Captured request = captured.get();
        assertThat(request.method()).isEqualTo("POST");
        assertThat(request.path()).isEqualTo("/v1/publish");
        assertThat(request.contentType()).startsWith("application/json");
        assertThat(request.timestamp()).isEqualTo(Long.toString(NOW));
        Mac mac = Mac.getInstance("HmacSHA256");
        mac.init(new SecretKeySpec(SECRET.getBytes(StandardCharsets.UTF_8), "HmacSHA256"));
        mac.update((request.timestamp() + ".").getBytes(StandardCharsets.US_ASCII));
        String expected = HexFormat.of().formatHex(mac.doFinal(request.body()));
        assertThat(request.signature()).isEqualTo(expected);

        JsonNode sent = json.readTree(request.body());
        assertThat(sent.get("requestId").asString()).isEqualTo(REQUEST_ID.toString());
        assertThat(sent.get("engineInstanceId").asString()).isEqualTo("hd-engine-01");
        assertThat(sent.get("definition")).isEqualTo(json.readTree(DEFINITION));
    }

    @Test
    void a200IsPublishedWithTheBindingParsed() {
        GatewayOutcome outcome = publish();

        assertThat(outcome).isInstanceOfSatisfying(GatewayOutcome.Published.class, published -> {
            GatewayBinding binding = published.result().binding();
            assertThat(binding.surveyId()).isEqualTo(900001);
            assertThat(binding.fingerprint()).isEqualTo("fm1:74e0d199d9839cdc");
            assertThat(binding.questions().getFirst().fields()).extracting(GatewayBinding.FieldBinding::fieldname)
                    .containsExactly("Q256", "Q256_Cother");
        });
    }

    @Test
    void a422IsAFailureAtTheValidateStage() {
        status = 422;
        reply = "{\"status\":\"rejected\",\"result\":" + result(false, "\"validate\"", "[\"E_MISSING_ANSWERS\"]",
                "null", "null") + "}";

        assertThat(publish()).isInstanceOfSatisfying(GatewayOutcome.Failed.class, failed -> {
            assertThat(failed.httpStatus()).isEqualTo(422);
            assertThat(failed.result().failedStage()).isEqualTo("validate");
            assertThat(failed.result().failures()).containsExactly("E_MISSING_ANSWERS");
        });
    }

    @Test
    void a502CarriesTheOrphanSurvey() {
        status = 502;
        reply = "{\"status\":\"failed\",\"result\":" + result(false, "\"activate\"", "[\"ROLLBACK FAILED: x\"]",
                "4242", "null") + "}";

        assertThat(publish()).isInstanceOfSatisfying(GatewayOutcome.Failed.class, failed -> {
            assertThat(failed.httpStatus()).isEqualTo(502);
            assertThat(failed.result().orphanSurveyId()).isEqualTo(4242);
            assertThat(failed.result().rolledBack()).isFalse();
        });
    }

    @Test
    void a409InProgressIsAnUnknownOutcome() {
        status = 409;
        reply = "{\"status\":\"conflict\",\"error\":\"publish_in_progress\"}";

        assertThat(publish()).isInstanceOf(GatewayOutcome.Unknown.class);
    }

    @Test
    void a404Or401IsARefusalThatNeverTouchedTheEngine() {
        status = 404;
        reply = "{\"error\":\"unknown_engine_instance\"}";
        assertThat(publish()).isEqualTo(new GatewayOutcome.Refused(404, "unknown_engine_instance"));

        status = 401;
        reply = "{\"error\":\"bad_signature\"}";
        assertThat(publish()).isEqualTo(new GatewayOutcome.Refused(401, "bad_signature"));
    }

    @Test
    void a410MeansTheStoredReceiptAgedOutAndCarriesTheEngineSurvey() {
        status = 410;
        reply = """
                {"status":"expired","error":"result_expired",
                 "expired":{"originalStatus":200,"createdAt":"2027-01-15T08:00:00Z",
                            "retainedSeconds":604800,"surveyId":511001}}
                """;

        assertThat(publish()).isEqualTo(new GatewayOutcome.Expired(200, 511001, "2027-01-15T08:00:00Z"));
    }

    @Test
    void anExpiredRejectionHasNoEngineSurveyToCleanUp() {
        status = 410;
        reply = """
                {"status":"expired","error":"result_expired",
                 "expired":{"originalStatus":422,"createdAt":"2027-01-15T08:00:00Z",
                            "retainedSeconds":604800,"surveyId":null}}
                """;

        assertThat(publish()).isEqualTo(new GatewayOutcome.Expired(422, null, "2027-01-15T08:00:00Z"));
    }

    @Test
    void a410WithoutDetailsIsStillTerminalRatherThanUnknown() {
        // 重发只会再得到 410，所以细节读不出来也不能退回"结果未知"——那是一个永远走不完的重试环。
        status = 410;
        reply = "{\"status\":\"expired\"}";

        assertThat(publish()).isInstanceOfSatisfying(GatewayOutcome.Expired.class, expired -> {
            assertThat(expired.originalStatus()).isZero();
            assertThat(expired.surveyId()).isNull();
        });
    }

    @Test
    void anUnexpectedStatusOrAnUnreadable200IsUnknown() {
        status = 500;
        reply = "oops";
        assertThat(publish()).isInstanceOf(GatewayOutcome.Unknown.class);

        status = 200;
        reply = "{\"status\":\"published\"}";
        assertThat(publish()).isInstanceOf(GatewayOutcome.Unknown.class);
    }

    @Test
    void aTimeoutIsUnknown() {
        delayMillis = 1_500;

        GatewayOutcome outcome = client(Duration.ofMillis(300))
                .publish(new GatewayRequest(REQUEST_ID, "hd-engine-01", DEFINITION));

        assertThat(outcome).isInstanceOf(GatewayOutcome.Unknown.class);
    }

    @Test
    void anUnreachableGatewayIsUnknown() {
        server.stop(0);

        assertThat(publish()).isInstanceOf(GatewayOutcome.Unknown.class);
    }

    @Test
    void aMissingOrShortSecretMeansNotConfigured() {
        URI base = URI.create("http://127.0.0.1:1");
        Clock clock = Clock.systemUTC();

        assertThat(new HttpPublishGatewayClient(base, "short", Duration.ofSeconds(1), clock, json).isConfigured())
                .isFalse();
        assertThat(new HttpPublishGatewayClient(null, SECRET, Duration.ofSeconds(1), clock, json).isConfigured())
                .isFalse();
        assertThat(client(Duration.ofSeconds(1)).isConfigured()).isTrue();
    }
}
