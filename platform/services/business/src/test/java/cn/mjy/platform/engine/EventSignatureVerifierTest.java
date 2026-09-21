package cn.mjy.platform.engine;

import static org.assertj.core.api.Assertions.assertThat;

import cn.mjy.platform.engine.EventSignatureVerifier.Verdict;
import cn.mjy.platform.engine.support.PhpEventSigner;
import java.nio.charset.StandardCharsets;
import java.time.Clock;
import java.time.Instant;
import java.time.ZoneOffset;
import org.junit.jupiter.api.Test;

/**
 * 引擎事件签名校验：以请求头声明的实例的派生密钥，HMAC-SHA256(十六进制) 覆盖 "时间戳.原始请求体"，
 * 并拒绝重放窗口外的时间戳。签名一律按原始字节计算，任何对请求体的改动（哪怕只是 JSON 等价的改写）都必须被拒绝；
 * 用别的实例的密钥、或直接用主密钥签名都必须被拒绝。
 */
class EventSignatureVerifierTest {

    private static final String MASTER = "unit-test-engine-secret-at-least-32-bytes";
    private static final String INSTANCE = "engine-x";
    private static final String OTHER_INSTANCE = "engine-y";
    private static final long NOW = 1_790_000_000L;
    private static final byte[] BODY = "{\"events\":[]}".getBytes(StandardCharsets.UTF_8);

    private final Clock clock = Clock.fixed(Instant.ofEpochSecond(NOW), ZoneOffset.UTC);
    private final EventSignatureVerifier verifier = new EventSignatureVerifier(new EngineEventKeys(MASTER), clock);

    private static String secretOf(String instance) {
        return PhpEventSigner.deriveInstanceSecret(MASTER, instance);
    }

    private static String signAs(String instance, String timestamp, byte[] body) {
        return PhpEventSigner.sign(secretOf(instance), timestamp, body);
    }

    @Test
    void acceptsARequestSignedTheWayThePhpSenderSignsIt() {
        String timestamp = Long.toString(NOW);

        Verdict verdict = verifier.verify(INSTANCE, timestamp, signAs(INSTANCE, timestamp, BODY), BODY);

        assertThat(verdict).isEqualTo(Verdict.VALID);
    }

    @Test
    void acceptsAnUppercaseHexSignature() {
        String timestamp = Long.toString(NOW);
        String signature = signAs(INSTANCE, timestamp, BODY).toUpperCase();

        assertThat(verifier.verify(INSTANCE, timestamp, signature, BODY)).isEqualTo(Verdict.VALID);
    }

    @Test
    void rejectsARequestClaimingAnotherInstanceThanTheKeyItWasSignedWith() {
        String timestamp = Long.toString(NOW);
        String signedByX = signAs(INSTANCE, timestamp, BODY);

        assertThat(verifier.verify(OTHER_INSTANCE, timestamp, signedByX, BODY)).isEqualTo(Verdict.BAD_SIGNATURE);
    }

    @Test
    void rejectsARequestSignedWithTheMasterSecretItself() {
        String timestamp = Long.toString(NOW);
        String signature = PhpEventSigner.sign(MASTER, timestamp, BODY);

        assertThat(verifier.verify(INSTANCE, timestamp, signature, BODY)).isEqualTo(Verdict.BAD_SIGNATURE);
    }

    @Test
    void rejectsASignatureMadeWithAnotherSecret() {
        String timestamp = Long.toString(NOW);
        String signature = PhpEventSigner.sign("another-secret-that-is-also-32-bytes-long", timestamp, BODY);

        assertThat(verifier.verify(INSTANCE, timestamp, signature, BODY)).isEqualTo(Verdict.BAD_SIGNATURE);
    }

    @Test
    void rejectsABodyTamperedAfterSigning() {
        String timestamp = Long.toString(NOW);
        String signature = signAs(INSTANCE, timestamp, BODY);
        byte[] tampered = "{\"events\": []}".getBytes(StandardCharsets.UTF_8);

        assertThat(verifier.verify(INSTANCE, timestamp, signature, tampered)).isEqualTo(Verdict.BAD_SIGNATURE);
    }

    @Test
    void rejectsATimestampSwappedAfterSigning() {
        String signedAt = Long.toString(NOW - 10);
        String signature = signAs(INSTANCE, signedAt, BODY);

        assertThat(verifier.verify(INSTANCE, Long.toString(NOW), signature, BODY)).isEqualTo(Verdict.BAD_SIGNATURE);
    }

    @Test
    void acceptsTimestampsAtTheEdgeOfTheReplayWindow() {
        long window = EventSignatureVerifier.REPLAY_WINDOW.toSeconds();
        String past = Long.toString(NOW - window);
        String future = Long.toString(NOW + window);

        assertThat(verifier.verify(INSTANCE, past, signAs(INSTANCE, past, BODY), BODY)).isEqualTo(Verdict.VALID);
        assertThat(verifier.verify(INSTANCE, future, signAs(INSTANCE, future, BODY), BODY)).isEqualTo(Verdict.VALID);
    }

    @Test
    void rejectsAStaleTimestampEvenWithAValidSignature() {
        String stale = Long.toString(NOW - EventSignatureVerifier.REPLAY_WINDOW.toSeconds() - 1);

        Verdict verdict = verifier.verify(INSTANCE, stale, signAs(INSTANCE, stale, BODY), BODY);

        assertThat(verdict).isEqualTo(Verdict.TIMESTAMP_OUT_OF_WINDOW);
    }

    @Test
    void rejectsATimestampTooFarInTheFuture() {
        String future = Long.toString(NOW + EventSignatureVerifier.REPLAY_WINDOW.toSeconds() + 1);

        Verdict verdict = verifier.verify(INSTANCE, future, signAs(INSTANCE, future, BODY), BODY);

        assertThat(verdict).isEqualTo(Verdict.TIMESTAMP_OUT_OF_WINDOW);
    }

    @Test
    void rejectsMissingOrMalformedHeaders() {
        String timestamp = Long.toString(NOW);
        String signature = signAs(INSTANCE, timestamp, BODY);

        assertThat(verifier.verify(INSTANCE, null, signature, BODY)).isEqualTo(Verdict.MISSING_HEADERS);
        assertThat(verifier.verify(INSTANCE, timestamp, null, BODY)).isEqualTo(Verdict.MISSING_HEADERS);
        assertThat(verifier.verify(INSTANCE, "", signature, BODY)).isEqualTo(Verdict.MISSING_HEADERS);
        assertThat(verifier.verify(INSTANCE, "12.5", signature, BODY)).isEqualTo(Verdict.MALFORMED_TIMESTAMP);
        assertThat(verifier.verify(INSTANCE, "+" + timestamp, signature, BODY)).isEqualTo(Verdict.MALFORMED_TIMESTAMP);
        assertThat(verifier.verify(INSTANCE, timestamp, "not-hex", BODY)).isEqualTo(Verdict.BAD_SIGNATURE);
        assertThat(verifier.verify(INSTANCE, timestamp, signature.substring(2), BODY)).isEqualTo(Verdict.BAD_SIGNATURE);
    }

    @Test
    void rejectsAMissingEngineInstanceWithItsOwnVerdict() {
        String timestamp = Long.toString(NOW);
        String signature = signAs(INSTANCE, timestamp, BODY);

        assertThat(verifier.verify(null, timestamp, signature, BODY)).isEqualTo(Verdict.MISSING_ENGINE_INSTANCE);
        assertThat(verifier.verify(" ", timestamp, signature, BODY)).isEqualTo(Verdict.MISSING_ENGINE_INSTANCE);
    }

    @Test
    void rejectsAnEngineInstanceThatCannotTravelSafelyInAHeader() {
        String timestamp = Long.toString(NOW);

        for (String malformed : new String[] {"华东-engine", "engine x", "e".repeat(65)}) {
            String signature = signAs(malformed, timestamp, BODY);
            assertThat(verifier.verify(malformed, timestamp, signature, BODY))
                    .as(malformed).isEqualTo(Verdict.MALFORMED_ENGINE_INSTANCE);
        }
    }

    @Test
    void aMissingOrShortSecretRejectsEverythingInsteadOfAcceptingUnsignedRequests() {
        EventSignatureVerifier unconfigured = new EventSignatureVerifier(new EngineEventKeys(""), clock);
        EventSignatureVerifier weak = new EventSignatureVerifier(new EngineEventKeys("short"), clock);
        String timestamp = Long.toString(NOW);

        assertThat(unconfigured.isConfigured()).isFalse();
        assertThat(unconfigured.verify(INSTANCE, timestamp, "00", BODY))
                .isEqualTo(Verdict.NOT_CONFIGURED);
        assertThat(weak.verify(INSTANCE, timestamp, PhpEventSigner.sign("short", timestamp, BODY), BODY))
                .isEqualTo(Verdict.NOT_CONFIGURED);
    }
}
