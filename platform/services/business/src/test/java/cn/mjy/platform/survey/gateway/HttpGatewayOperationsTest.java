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

/** 契约 publish-gateway-v1.2：POST /v1/close 与 POST /v1/drift-check 的请求形状、签名与应答归类。 */
class HttpGatewayOperationsTest {

    private static final String SECRET = "contract-test-secret-0123456789abcdef";
    private static final long NOW = 1_790_000_000L;
    private static final UUID REQUEST_ID = UUID.fromString("0b0d3f2e-0000-4000-8000-000000000002");
    private static final String BINDING = "{\"engineInstance\":\"hd-engine-01\",\"surveyId\":900001}";

    private final JsonMapper json = JsonMapper.builder().build();
    private final AtomicReference<String> path = new AtomicReference<>();
    private final AtomicReference<byte[]> body = new AtomicReference<>();
    private final AtomicReference<String[]> signature = new AtomicReference<>();
    private HttpServer server;
    private volatile int status;
    private volatile String reply;

    @BeforeEach
    void startGateway() throws IOException {
        server = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
        server.createContext("/", exchange -> {
            path.set(exchange.getRequestURI().getPath());
            body.set(exchange.getRequestBody().readAllBytes());
            signature.set(new String[] {exchange.getRequestHeaders().getFirst("X-Pubgw-Timestamp"),
                exchange.getRequestHeaders().getFirst("X-Pubgw-Signature")});
            byte[] out = reply.getBytes(StandardCharsets.UTF_8);
            exchange.sendResponseHeaders(status, out.length);
            try (OutputStream stream = exchange.getResponseBody()) {
                stream.write(out);
            }
        });
        server.start();
    }

    @AfterEach
    void stopGateway() {
        server.stop(0);
    }

    private HttpPublishGatewayClient client() {
        URI base = URI.create("http://127.0.0.1:" + server.getAddress().getPort());
        return new HttpPublishGatewayClient(base, SECRET, Duration.ofSeconds(5),
                Clock.fixed(Instant.ofEpochSecond(NOW), ZoneOffset.UTC), json);
    }

    private void answer(int httpStatus, String json) {
        status = httpStatus;
        reply = json;
    }

    private CloseOutcome close() {
        return client().close(new GatewayCloseRequest(REQUEST_ID, "hd-engine-01", 900001));
    }

    private DriftOutcome drift(String binding) {
        return client().driftCheck(new GatewayDriftRequest("hd-engine-01", 900001, "fm1:74e0d199d9839cdc", binding));
    }

    private static final String MATCH = """
            {"status":"match","result":{"surveyId":900001,"expectedFingerprint":"fm1:74e0d199d9839cdc",
             "currentFingerprint":"fm1:74e0d199d9839cdc","drifted":false,"active":"Y","renamed":[],"issues":[]}}
            """;

    @Test
    void closeSendsTheContractBodySigned() throws Exception {
        answer(200, "{\"status\":\"closed\",\"result\":{\"surveyId\":900001,\"expires\":\"2026-09-21 08:00:00\","
                + "\"alreadyClosed\":false}}");

        CloseOutcome outcome = close();

        assertThat(outcome).isEqualTo(new CloseOutcome.Closed("2026-09-21 08:00:00", false));
        assertThat(path.get()).isEqualTo("/v1/close");
        JsonNode sent = json.readTree(body.get());
        assertThat(sent.size()).isEqualTo(3);
        assertThat(sent.get("requestId").asString()).isEqualTo(REQUEST_ID.toString());
        assertThat(sent.get("engineInstanceId").asString()).isEqualTo("hd-engine-01");
        assertThat(sent.get("surveyId").asInt()).isEqualTo(900001);
        assertThat(signature.get()[0]).isEqualTo(Long.toString(NOW));
        Mac mac = Mac.getInstance("HmacSHA256");
        mac.init(new SecretKeySpec(SECRET.getBytes(StandardCharsets.UTF_8), "HmacSHA256"));
        mac.update((NOW + ".").getBytes(StandardCharsets.US_ASCII));
        assertThat(signature.get()[1]).isEqualTo(HexFormat.of().formatHex(mac.doFinal(body.get())));
    }

    @Test
    void anyCloseAnswerButAConsistent200IsNotClosed() {
        answer(502, "{\"status\":\"failed\",\"error\":\"E_SURVEY_MISSING\",\"detail\":\"gone\"}");
        assertThat(close()).isInstanceOfSatisfying(CloseOutcome.NotClosed.class,
                n -> assertThat(n.reason()).contains("E_SURVEY_MISSING"));

        answer(200, "{\"status\":\"closed\",\"result\":{\"surveyId\":1,\"expires\":\"x\",\"alreadyClosed\":false}}");
        assertThat(close()).isInstanceOf(CloseOutcome.NotClosed.class);

        answer(200, "not json");
        assertThat(close()).isInstanceOf(CloseOutcome.NotClosed.class);
    }

    @Test
    void driftCheckSendsTheStoredBindingVerbatim() throws Exception {
        answer(200, MATCH);

        DriftOutcome outcome = drift(BINDING);

        assertThat(outcome).isInstanceOfSatisfying(DriftOutcome.Checked.class, c -> {
            assertThat(c.drifted()).isFalse();
            assertThat(c.currentFingerprint()).isEqualTo("fm1:74e0d199d9839cdc");
        });
        assertThat(path.get()).isEqualTo("/v1/drift-check");
        JsonNode sent = json.readTree(body.get());
        assertThat(sent.get("binding")).isEqualTo(json.readTree(BINDING));
        assertThat(sent.get("expectedFingerprint").asString()).isEqualTo("fm1:74e0d199d9839cdc");
    }

    @Test
    void driftCheckWithoutABindingOmitsTheField() {
        answer(200, MATCH);

        drift(null);

        assertThat(json.readTree(body.get()).has("binding")).isFalse();
    }

    @Test
    void aDriftAnswerCarriesIssuesAndRenames() {
        answer(200, """
                {"status":"drift","result":{"surveyId":900001,"expectedFingerprint":"fm1:74e0d199d9839cdc",
                 "currentFingerprint":"fm1:0e1235f2e2bc5a95","drifted":true,"active":"Y",
                 "renamed":[{"uuid":"33333333-0001-4111-8111-000000000001","from":"QSINGLE","to":"QDRIFTED"}],
                 "issues":[{"code":"E_CODE_DRIFT","detail":"renamed"}]}}
                """);

        assertThat(drift(BINDING)).isInstanceOfSatisfying(DriftOutcome.Checked.class, c -> {
            assertThat(c.drifted()).isTrue();
            assertThat(c.issues()).extracting(DriftOutcome.Issue::code).containsExactly("E_CODE_DRIFT");
            assertThat(c.renamed()).extracting(DriftOutcome.Rename::to).containsExactly("QDRIFTED");
        });
    }

    @Test
    void anUnreadableOrFailedDriftAnswerIsUnavailableNeverAMatch() {
        answer(502, "{\"status\":\"failed\",\"error\":\"engine_error\",\"detail\":\"x\"}");
        assertThat(drift(BINDING)).isInstanceOf(DriftOutcome.Unavailable.class);

        answer(200, "{\"status\":\"match\",\"result\":{\"surveyId\":900001,\"drifted\":true,\"renamed\":[],"
                + "\"issues\":[]}}");
        assertThat(drift(BINDING)).isInstanceOf(DriftOutcome.Unavailable.class);

        answer(200, "{\"status\":\"drift\",\"result\":{\"surveyId\":900001,\"drifted\":true,\"renamed\":[],"
                + "\"issues\":[]}}");
        assertThat(drift(BINDING)).isInstanceOf(DriftOutcome.Unavailable.class);
    }
}
