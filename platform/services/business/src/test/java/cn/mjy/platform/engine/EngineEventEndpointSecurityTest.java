package cn.mjy.platform.engine;

import static cn.mjy.platform.engine.support.SignedEventRequests.PATH;
import static cn.mjy.platform.engine.support.SignedEventRequests.SECRET;
import static cn.mjy.platform.engine.support.SignedEventRequests.body;
import static cn.mjy.platform.engine.support.SignedEventRequests.raw;
import static cn.mjy.platform.engine.support.SignedEventRequests.signed;
import static cn.mjy.platform.engine.support.SignedEventRequests.signedAt;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

import cn.mjy.platform.engine.support.EngineEventsIntegrationTest;
import cn.mjy.platform.engine.support.PhpEventSigner;
import cn.mjy.platform.support.TestTokens;
import java.nio.charset.StandardCharsets;
import java.time.Instant;
import java.util.List;
import java.util.UUID;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.test.web.servlet.MockMvc;

/**
 * /internal/engine-events 是服务间接口：只认 HMAC 签名，不认用户 JWT；
 * 签名错误、请求体被改、时间戳超出重放窗口一律 401，且不进入业务处理。
 */
@EngineEventsIntegrationTest
class EngineEventEndpointSecurityTest {

    private static final byte[] EMPTY_BATCH = "{\"events\":[]}".getBytes(StandardCharsets.UTF_8);

    @Autowired
    private MockMvc mvc;

    @Autowired
    private TestTokens tokens;

    @Test
    void aCorrectlySignedBatchIsAccepted() throws Exception {
        mvc.perform(signed(EMPTY_BATCH))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.received").value(0));
    }

    @Test
    void anUnsignedRequestIsRejected() throws Exception {
        mvc.perform(raw(EMPTY_BATCH, null, null))
                .andExpect(status().isUnauthorized())
                .andExpect(jsonPath("$.error").value("missing_signature"));
    }

    @Test
    void aSignatureMadeWithTheWrongSecretIsRejected() throws Exception {
        String timestamp = Long.toString(Instant.now().getEpochSecond());
        String signature = PhpEventSigner.sign("wrong-secret-that-is-at-least-32-bytes-long", timestamp, EMPTY_BATCH);

        mvc.perform(raw(EMPTY_BATCH, timestamp, signature))
                .andExpect(status().isUnauthorized())
                .andExpect(jsonPath("$.error").value("invalid_signature"));
    }

    @Test
    void aBodyTamperedAfterSigningIsRejected() throws Exception {
        String timestamp = Long.toString(Instant.now().getEpochSecond());
        byte[] original = body();
        String signature = PhpEventSigner.sign(SECRET, timestamp, original);
        byte[] tampered = "{\"events\":[{\"eventId\":\"x\"}]}".getBytes(StandardCharsets.UTF_8);

        mvc.perform(raw(tampered, timestamp, signature))
                .andExpect(status().isUnauthorized())
                .andExpect(jsonPath("$.error").value("invalid_signature"));
    }

    @Test
    void aStaleButOtherwiseValidRequestIsRejectedAsAReplay() throws Exception {
        long stale = Instant.now().getEpochSecond() - EventSignatureVerifier.REPLAY_WINDOW.toSeconds() - 60;

        mvc.perform(signedAt(stale, EMPTY_BATCH))
                .andExpect(status().isUnauthorized())
                .andExpect(jsonPath("$.error").value("timestamp_out_of_window"));
    }

    @Test
    void aTimestampFarInTheFutureIsRejected() throws Exception {
        long future = Instant.now().getEpochSecond() + EventSignatureVerifier.REPLAY_WINDOW.toSeconds() + 60;

        mvc.perform(signedAt(future, EMPTY_BATCH))
                .andExpect(status().isUnauthorized())
                .andExpect(jsonPath("$.error").value("timestamp_out_of_window"));
    }

    @Test
    void aUserJwtDoesNotOpenTheInternalEndpoint() throws Exception {
        String jwt = tokens.issue("user-1", UUID.randomUUID(), List.of("admin"));

        mvc.perform(post(PATH).contentType("application/json").content(EMPTY_BATCH)
                        .header("Authorization", "Bearer " + jwt))
                .andExpect(status().isUnauthorized());
    }

    @Test
    void anOversizedBodyIsRejectedBeforeItIsBuffered() throws Exception {
        byte[] huge = new byte[EngineEventSignatureFilter.MAX_BODY_BYTES + 1];

        mvc.perform(signed(huge)).andExpect(status().isPayloadTooLarge());
    }

    @Test
    void theRestOfTheApiStillRequiresAJwt() throws Exception {
        String timestamp = Long.toString(Instant.now().getEpochSecond());

        mvc.perform(post("/v1/me").header("X-Mjy-Timestamp", timestamp)
                        .header("X-Mjy-Signature", PhpEventSigner.sign(SECRET, timestamp, new byte[0])))
                .andExpect(status().isUnauthorized());
    }
}
