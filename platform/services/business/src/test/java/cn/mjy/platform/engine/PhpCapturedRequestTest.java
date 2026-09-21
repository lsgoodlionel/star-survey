package cn.mjy.platform.engine;

import static cn.mjy.platform.engine.support.SignedEventRequests.signed;
import static org.assertj.core.api.Assertions.assertThat;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

import cn.mjy.platform.engine.EventSignatureVerifier.Verdict;
import cn.mjy.platform.engine.support.EngineEventsIntegrationTest;
import cn.mjy.platform.engine.support.EngineRows;
import cn.mjy.platform.engine.support.FakeEngineInstanceDirectory;
import cn.mjy.platform.engine.support.PhpEnvelope;
import cn.mjy.platform.engine.support.SignedEventRequests;
import cn.mjy.platform.shared.TenantId;
import java.io.IOException;
import java.io.InputStream;
import java.nio.charset.StandardCharsets;
import java.time.Clock;
import java.time.Instant;
import java.time.ZoneOffset;
import java.util.Arrays;
import java.util.Map;
import java.util.stream.Collectors;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.test.web.servlet.MockMvc;

/**
 * 真实 PHP 产生的请求（金样）：{@code engine-contract/php-relay-request.*} 由 PHP 8.3 运行
 * 仓库里真实的 {@code MjyEventRelay} + {@code MjyHttpEventTransport} 抓包得到
 * （重新生成见 {@code engine-contract/capture/regenerate.sh}）。
 *
 * <p>与 {@link EngineEventContractTest}（Java 重现 PHP 逻辑）互补：这里证明平台接受的是 PHP 实际发出的字节，
 * 也证明那份 Java 重现与真实 PHP 输出逐字节一致。
 *
 * <p>抓包时 PHP 端用 {@code hash_hmac} 从主密钥派生实例密钥，再交给真实的发送端签名；
 * 这里用平台的 {@link EngineEventKeys} 从同一主密钥派生并验签，因而同时证明两端的派生算法一致。
 */
@EngineEventsIntegrationTest
class PhpCapturedRequestTest {

    /** 实例标识含 "/"（PHP 输出 {@code \/}），代次含非 ASCII，覆盖 JSON 编码的两类差异。 */
    private static final String INSTANCE = "hd/engine-01";
    private static final String GENERATION = "gen-华东/e2e";

    @Autowired
    private MockMvc mvc;

    @Autowired
    private FakeEngineInstanceDirectory directory;

    @Autowired
    private EngineRows rows;

    @Test
    void theCapturedSignatureVerifiesAtTheMomentItWasSent() throws IOException {
        Map<String, String> headers = capturedHeaders();
        String timestamp = headers.get("X-Mjy-Timestamp");
        Clock atSendTime = Clock.fixed(Instant.ofEpochSecond(Long.parseLong(timestamp)), ZoneOffset.UTC);
        EventSignatureVerifier verifier = new EventSignatureVerifier(
                new EngineEventKeys(SignedEventRequests.MASTER_SECRET), atSendTime);

        Verdict verdict = verifier.verify(headers.get("X-Mjy-Engine-Instance"), timestamp,
                headers.get("X-Mjy-Signature"), capturedBody());

        assertThat(verdict).isEqualTo(Verdict.VALID);
        assertThat(headers).containsEntry("Content-Type", "application/json")
                .containsEntry("X-Mjy-Engine-Instance", INSTANCE);
    }

    @Test
    void theJavaReimplementationOfThePhpSenderProducesTheSameBytes() throws IOException {
        byte[] reimplemented = SignedEventRequests.body(
                PhpEnvelope.event(PhpEnvelope.SAVED).eventId("6f9a1c2e-3b4d-4e5f-8a6b-7c8d9e0f1a2b")
                        .instance(INSTANCE).survey(880001).generation(GENERATION).response(101)
                        .source("hook").occurredAt("2026-09-21 07:15:40"),
                PhpEnvelope.event(PhpEnvelope.COMPLETED).eventId("0b1c2d3e-4f50-4a61-9b72-8c93d4e5f607")
                        .instance(INSTANCE).survey(880001).generation(GENERATION).response(101)
                        .source("scanner").occurredAt("2026-09-21 07:15:42"));

        assertThat(new String(reimplemented, StandardCharsets.UTF_8))
                .isEqualTo(new String(capturedBody(), StandardCharsets.UTF_8));
    }

    @Test
    void theEndpointAcceptsTheGenuinePhpBody() throws Exception {
        // 金样的时间戳早已超出重放窗口，这里用当前时间重新签名；请求体仍是 PHP 原样字节。
        TenantId tenant = TenantId.random();
        directory.register(INSTANCE, tenant);

        mvc.perform(signed(INSTANCE, capturedBody()))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.accepted").value(2));

        assertThat(rows.states(tenant, INSTANCE, 880001, GENERATION, 101)).containsExactly("engine_completed");
        assertThat(rows.outboxTypes(tenant)).containsExactly("response.engine_completed");
    }

    private static byte[] capturedBody() throws IOException {
        return resource("php-relay-request.body");
    }

    private static Map<String, String> capturedHeaders() throws IOException {
        String text = new String(resource("php-relay-request.headers"), StandardCharsets.UTF_8);
        return Arrays.stream(text.split("\n"))
                .filter(line -> line.contains(": "))
                .map(line -> line.split(": ", 2))
                .collect(Collectors.toMap(parts -> parts[0], parts -> parts[1], (a, b) -> b));
    }

    private static byte[] resource(String name) throws IOException {
        try (InputStream in = PhpCapturedRequestTest.class.getResourceAsStream("/engine-contract/" + name)) {
            if (in == null) {
                throw new IOException("missing test resource engine-contract/" + name);
            }
            return in.readAllBytes();
        }
    }
}
