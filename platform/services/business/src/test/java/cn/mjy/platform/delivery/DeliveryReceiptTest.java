package cn.mjy.platform.delivery;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import cn.mjy.platform.delivery.ReceiptService.ReceiptRejectedException;
import cn.mjy.platform.response.ResponseFixture.Published;
import cn.mjy.platform.shared.TenantId;
import java.nio.charset.StandardCharsets;
import java.security.GeneralSecurityException;
import java.time.Instant;
import java.util.HexFormat;
import java.util.UUID;
import javax.crypto.Mac;
import javax.crypto.spec.SecretKeySpec;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;

/** 渠道商回执（R05-04、R05-05）：验签、防重放，以及"重复回执不重复计费"。 */
@DeliveryIntegrationTest
class DeliveryReceiptTest {

    private static final String PROVIDER = "fake-email";

    @Autowired
    private DeliveryFixture fixture;

    @Autowired
    private ReceiptService receipts;

    @Autowired
    private DeliveryKeys keys;

    @Autowired
    private FakeEmailProvider email;

    @Autowired
    private DeliveryTaskService tasks;

    private Published p;
    private TaskView task;

    @BeforeEach
    void aSentTask() {
        email.reset();
        p = fixture.publishedSurvey();
        task = fixture.emailTask(p, 3);
        fixture.run(p.tenant(), task.id());
    }

    @Test
    void aDeliveredReceiptIsCountedOnceNoMatterHowOftenItIsRedelivered() {
        String body = events("evt-1", "delivered", idempotencyKey(1));

        ReceiptService.Result first = post(p.tenant(), body);
        ReceiptService.Result again = post(p.tenant(), body);
        ReceiptService.Result third = post(p.tenant(), body);

        assertThat(first.applied()).isEqualTo(1);
        assertThat(again.applied()).isZero();
        assertThat(third.applied()).isZero();
        assertThat(fixture.billedUnits(p.tenant(), task.id())).isEqualTo(1);
    }

    @Test
    void eachDistinctReceiptBillsOnceSoTheTotalMatchesTheNumberOfMessages() {
        post(p.tenant(), events("evt-a", "delivered", idempotencyKey(1)));
        post(p.tenant(), events("evt-b", "delivered", idempotencyKey(2)));
        post(p.tenant(), events("evt-b", "delivered", idempotencyKey(2)));
        post(p.tenant(), events("evt-c", "delivered", idempotencyKey(3)));

        assertThat(fixture.billedUnits(p.tenant(), task.id())).isEqualTo(3);
    }

    /** 未送达不计费：多收不如少收。 */
    @Test
    void aBouncedReceiptIsNotBilledAndSuppressesTheAddress() {
        String bounced = fixture.addressOf(p.tenant(), task.id(), 2);

        post(p.tenant(), events("evt-bounce", "bounced", idempotencyKey(2)));

        assertThat(fixture.billedUnits(p.tenant(), task.id())).isZero();
        assertThat(fixture.recipientViews(p.owner(), task.id()))
                .filteredOn(recipient -> recipient.state().equals("bounced"))
                .hasSize(1);

        // 退信之后同一个地址进了退订名单，之后的任务不再投递给他。
        email.reset();
        TaskView second = tasks.create(p.owner(), new DeliveryTaskService.NewTask(p.surveyId(),
                DeliveryChannel.EMAIL, null, "再试一次", DeliveryFixture.EMAIL_BODY,
                java.util.List.of(DeliveryTaskService.NewRecipient.of(bounced)), null));
        fixture.run(p.tenant(), second.id());

        assertThat(email.delivered()).isEmpty();
        assertThat(fixture.status(p.owner(), second.id()).skippedCount()).isEqualTo(1);
    }

    @Test
    void aReceiptWithoutAValidSignatureIsRejectedAndLeavesNoTrace() {
        String body = events("evt-x", "delivered", idempotencyKey(1));
        String timestamp = Long.toString(Instant.now().getEpochSecond());

        assertThatThrownBy(() -> receipts.accept(p.tenant(), PROVIDER, timestamp, "00".repeat(32), body.getBytes(
                StandardCharsets.UTF_8)))
                .isInstanceOf(ReceiptRejectedException.class);
        assertThat(fixture.billedUnits(p.tenant(), task.id())).isZero();
    }

    /** 另一个租户的密钥签出来的回执，验不过：密钥按 (租户, 渠道商) 派生。 */
    @Test
    void aReceiptSignedWithAnotherTenantsSecretIsRejected() {
        String body = events("evt-y", "delivered", idempotencyKey(1));
        String timestamp = Long.toString(Instant.now().getEpochSecond());
        String signature = sign(keys.receiptKey(TenantId.random(), PROVIDER), timestamp, body);

        assertThatThrownBy(() -> receipts.accept(p.tenant(), PROVIDER, timestamp, signature,
                body.getBytes(StandardCharsets.UTF_8)))
                .isInstanceOf(ReceiptRejectedException.class);
    }

    @Test
    void anOldTimestampIsRejectedEvenWithAValidSignature() {
        String body = events("evt-z", "delivered", idempotencyKey(1));
        String stale = Long.toString(Instant.now().getEpochSecond() - ReceiptService.REPLAY_WINDOW_SECONDS - 60);
        String signature = sign(keys.receiptKey(p.tenant(), PROVIDER), stale, body);

        assertThatThrownBy(() -> receipts.accept(p.tenant(), PROVIDER, stale, signature,
                body.getBytes(StandardCharsets.UTF_8)))
                .isInstanceOf(ReceiptRejectedException.class);
    }

    @Test
    void aReceiptForAnUnknownMessageIsAcceptedButChangesNothing() {
        ReceiptService.Result result = post(p.tenant(), events("evt-unknown", "delivered", UUID.randomUUID()
                + ":" + UUID.randomUUID()));

        assertThat(result.received()).isEqualTo(1);
        assertThat(result.applied()).isZero();
        assertThat(fixture.billedUnits(p.tenant(), task.id())).isZero();
    }

    @Test
    void anUnknownEventKindIsRejected() {
        String body = """
                {"events":[{"eventId":"evt-bad","kind":"teleported","idempotencyKey":"%s"}]}
                """.formatted(idempotencyKey(1));
        String timestamp = Long.toString(Instant.now().getEpochSecond());
        String signature = sign(keys.receiptKey(p.tenant(), PROVIDER), timestamp, body);

        assertThatThrownBy(() -> receipts.accept(p.tenant(), PROVIDER, timestamp, signature,
                body.getBytes(StandardCharsets.UTF_8)))
                .isInstanceOf(ReceiptRejectedException.class);
    }

    private ReceiptService.Result post(TenantId tenant, String body) {
        String timestamp = Long.toString(Instant.now().getEpochSecond());
        String signature = sign(keys.receiptKey(tenant, PROVIDER), timestamp, body);
        return receipts.accept(tenant, PROVIDER, timestamp, signature, body.getBytes(StandardCharsets.UTF_8));
    }

    private String idempotencyKey(int recipientIndex) {
        return task.id() + ":" + fixture.recipientIdOf(p.tenant(), task.id(), recipientIndex);
    }

    private static String events(String eventId, String kind, String idempotencyKey) {
        return """
                {"events":[{"eventId":"%s","kind":"%s","idempotencyKey":"%s","occurredAt":"2026-09-23T10:00:00Z"}]}
                """.formatted(eventId, kind, idempotencyKey);
    }

    private static String sign(SecretKeySpec key, String timestamp, String body) {
        try {
            Mac mac = Mac.getInstance("HmacSHA256");
            mac.init(key);
            mac.update((timestamp + ".").getBytes(StandardCharsets.US_ASCII));
            mac.update(body.getBytes(StandardCharsets.UTF_8));
            return HexFormat.of().formatHex(mac.doFinal());
        } catch (GeneralSecurityException e) {
            throw new IllegalStateException(e);
        }
    }
}
