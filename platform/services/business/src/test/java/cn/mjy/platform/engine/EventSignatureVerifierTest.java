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
 * 引擎事件签名校验：HMAC-SHA256(十六进制) 覆盖 "时间戳.原始请求体"，并拒绝重放窗口外的时间戳。
 * 签名一律按原始字节计算，任何对请求体的改动（哪怕只是 JSON 等价的改写）都必须被拒绝。
 */
class EventSignatureVerifierTest {

    private static final String SECRET = "unit-test-engine-secret-at-least-32-bytes";
    private static final long NOW = 1_790_000_000L;
    private static final byte[] BODY = "{\"events\":[]}".getBytes(StandardCharsets.UTF_8);

    private final Clock clock = Clock.fixed(Instant.ofEpochSecond(NOW), ZoneOffset.UTC);
    private final EventSignatureVerifier verifier = new EventSignatureVerifier(SECRET, clock);

    @Test
    void acceptsARequestSignedTheWayThePhpSenderSignsIt() {
        String timestamp = Long.toString(NOW);

        Verdict verdict = verifier.verify(timestamp, PhpEventSigner.sign(SECRET, timestamp, BODY), BODY);

        assertThat(verdict).isEqualTo(Verdict.VALID);
    }

    @Test
    void acceptsAnUppercaseHexSignature() {
        String timestamp = Long.toString(NOW);
        String signature = PhpEventSigner.sign(SECRET, timestamp, BODY).toUpperCase();

        assertThat(verifier.verify(timestamp, signature, BODY)).isEqualTo(Verdict.VALID);
    }

    @Test
    void rejectsASignatureMadeWithAnotherSecret() {
        String timestamp = Long.toString(NOW);
        String signature = PhpEventSigner.sign("another-secret-that-is-also-32-bytes-long", timestamp, BODY);

        assertThat(verifier.verify(timestamp, signature, BODY)).isEqualTo(Verdict.BAD_SIGNATURE);
    }

    @Test
    void rejectsABodyTamperedAfterSigning() {
        String timestamp = Long.toString(NOW);
        String signature = PhpEventSigner.sign(SECRET, timestamp, BODY);
        byte[] tampered = "{\"events\": []}".getBytes(StandardCharsets.UTF_8);

        assertThat(verifier.verify(timestamp, signature, tampered)).isEqualTo(Verdict.BAD_SIGNATURE);
    }

    @Test
    void rejectsATimestampSwappedAfterSigning() {
        String signedAt = Long.toString(NOW - 10);
        String signature = PhpEventSigner.sign(SECRET, signedAt, BODY);

        assertThat(verifier.verify(Long.toString(NOW), signature, BODY)).isEqualTo(Verdict.BAD_SIGNATURE);
    }

    @Test
    void acceptsTimestampsAtTheEdgeOfTheReplayWindow() {
        long window = EventSignatureVerifier.REPLAY_WINDOW.toSeconds();
        String past = Long.toString(NOW - window);
        String future = Long.toString(NOW + window);

        assertThat(verifier.verify(past, PhpEventSigner.sign(SECRET, past, BODY), BODY)).isEqualTo(Verdict.VALID);
        assertThat(verifier.verify(future, PhpEventSigner.sign(SECRET, future, BODY), BODY)).isEqualTo(Verdict.VALID);
    }

    @Test
    void rejectsAStaleTimestampEvenWithAValidSignature() {
        String stale = Long.toString(NOW - EventSignatureVerifier.REPLAY_WINDOW.toSeconds() - 1);

        Verdict verdict = verifier.verify(stale, PhpEventSigner.sign(SECRET, stale, BODY), BODY);

        assertThat(verdict).isEqualTo(Verdict.TIMESTAMP_OUT_OF_WINDOW);
    }

    @Test
    void rejectsATimestampTooFarInTheFuture() {
        String future = Long.toString(NOW + EventSignatureVerifier.REPLAY_WINDOW.toSeconds() + 1);

        Verdict verdict = verifier.verify(future, PhpEventSigner.sign(SECRET, future, BODY), BODY);

        assertThat(verdict).isEqualTo(Verdict.TIMESTAMP_OUT_OF_WINDOW);
    }

    @Test
    void rejectsMissingOrMalformedHeaders() {
        String timestamp = Long.toString(NOW);
        String signature = PhpEventSigner.sign(SECRET, timestamp, BODY);

        assertThat(verifier.verify(null, signature, BODY)).isEqualTo(Verdict.MISSING_HEADERS);
        assertThat(verifier.verify(timestamp, null, BODY)).isEqualTo(Verdict.MISSING_HEADERS);
        assertThat(verifier.verify("", signature, BODY)).isEqualTo(Verdict.MISSING_HEADERS);
        assertThat(verifier.verify("12.5", signature, BODY)).isEqualTo(Verdict.MALFORMED_TIMESTAMP);
        assertThat(verifier.verify("+" + timestamp, signature, BODY)).isEqualTo(Verdict.MALFORMED_TIMESTAMP);
        assertThat(verifier.verify(timestamp, "not-hex", BODY)).isEqualTo(Verdict.BAD_SIGNATURE);
        assertThat(verifier.verify(timestamp, signature.substring(2), BODY)).isEqualTo(Verdict.BAD_SIGNATURE);
    }

    @Test
    void aMissingOrShortSecretRejectsEverythingInsteadOfAcceptingUnsignedRequests() {
        EventSignatureVerifier unconfigured = new EventSignatureVerifier("", clock);
        EventSignatureVerifier weak = new EventSignatureVerifier("short", clock);
        String timestamp = Long.toString(NOW);

        assertThat(unconfigured.isConfigured()).isFalse();
        assertThat(unconfigured.verify(timestamp, "00", BODY))
                .isEqualTo(Verdict.NOT_CONFIGURED);
        assertThat(weak.verify(timestamp, PhpEventSigner.sign("short", timestamp, BODY), BODY))
                .isEqualTo(Verdict.NOT_CONFIGURED);
    }
}
