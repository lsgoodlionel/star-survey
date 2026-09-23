package cn.mjy.platform.delivery;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import cn.mjy.platform.delivery.DeliveryTaskService.NewRecipient;
import cn.mjy.platform.delivery.DeliveryTaskService.NewTask;
import cn.mjy.platform.delivery.FakeDeliveryProvider.SimulatedCrash;
import cn.mjy.platform.response.ResponseFixture.Published;
import java.util.List;
import java.util.Map;
import java.util.UUID;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;

/** 批量投放（R05-04、R05-05）：恢复不重发、退订被遵守、限速、地址校验与脱敏。 */
@DeliveryIntegrationTest
class DeliverySendTest {

    private static final int RECIPIENTS = 12;

    @Autowired
    private DeliveryFixture fixture;

    @Autowired
    private DeliveryTaskService tasks;

    @Autowired
    private DeliveryWorker worker;

    @Autowired
    private UnsubscribeService unsubscribes;

    @Autowired
    private UnsubscribeTokens tokens;

    @Autowired
    private FakeEmailProvider email;

    @Autowired
    private FakeSmsProvider sms;

    private Published p;

    @BeforeEach
    void publishedSurvey() {
        email.reset();
        sms.reset();
        p = fixture.publishedSurvey();
    }

    @Test
    void everyRecipientGetsOneMessageWithTheirOwnSignedLink() {
        TaskView task = fixture.emailTask(p, RECIPIENTS);

        fixture.run(p.tenant(), task.id());

        assertThat(email.delivered()).hasSize(RECIPIENTS);
        assertThat(fixture.status(p.owner(), task.id()).sentCount()).isEqualTo(RECIPIENTS);
        assertThat(fixture.status(p.owner(), task.id()).status()).isEqualTo("completed");
        // 每人的链接各不相同：带各自的邀请码与应答者标识。
        assertThat(email.delivered().stream().map(FakeDeliveryProvider.Delivered::body).distinct())
                .hasSize(RECIPIENTS);
        assertThat(email.delivered()).allSatisfy(delivered -> assertThat(delivered.body())
                .contains("/index.php/" + p.sid())
                .contains("rk=")
                .contains("token=")
                .contains("sig=")
                .contains("https://survey.example" + UnsubscribeService.PATH));
    }

    /** 进程在发送途中死掉：恢复后每人仍然只收到一条——幂等键不变，渠道商据此去重。 */
    @Test
    void aCrashMidTaskResumesWithoutSendingAnythingTwice() {
        TaskView task = fixture.emailTask(p, RECIPIENTS);
        email.crashAfter(5);

        assertThatThrownBy(() -> fixture.run(p.tenant(), task.id())).isInstanceOf(SimulatedCrash.class);

        // 崩溃点之前已经投出去 5 条，第 6 条的结果未知（收件人停在 sending）。
        assertThat(email.delivered()).hasSize(5);
        assertThat(fixture.recipientViews(p.owner(), task.id()))
                .filteredOn(recipient -> recipient.state().equals("sending"))
                .hasSize(1);

        email.stopCrashing();
        fixture.run(p.tenant(), task.id());

        assertThat(email.delivered()).hasSize(RECIPIENTS);
        // 第 6 条被重发了一次，但渠道商按幂等键只投递一次。
        assertThat(email.callCount()).isGreaterThan(RECIPIENTS);
        assertThat(fixture.status(p.owner(), task.id()).sentCount()).isEqualTo(RECIPIENTS);
        assertThat(fixture.recipientViews(p.owner(), task.id()))
                .allSatisfy(recipient -> assertThat(recipient.state()).isEqualTo("sent"));
    }

    /** 同一个任务再跑一遍，什么也不会再发：收件人都已经是终态。 */
    @Test
    void runningAFinishedTaskAgainSendsNothing() {
        TaskView task = fixture.emailTask(p, 3);
        fixture.run(p.tenant(), task.id());
        int callsAfterFirstRun = email.callCount();

        fixture.run(p.tenant(), task.id());

        assertThat(email.callCount()).isEqualTo(callsAfterFirstRun);
        assertThat(email.delivered()).hasSize(3);
    }

    @Test
    void anUnsubscribedAddressIsSkippedAndTheUnsubscribeCannotBeUndoneByALaterTask() {
        TaskView first = fixture.emailTask(p, 3);
        fixture.run(p.tenant(), first.id());
        String address = fixture.addressOf(p.tenant(), first.id(), 2);
        UUID recipientId = fixture.recipientIdOf(p.tenant(), first.id(), 2);

        // 收件人点了邮件里的退订链接。
        String token = unsubscribeToken(p, first.id(), recipientId);
        assertThat(unsubscribes.unsubscribe(token, "trace").alreadyUnsubscribed()).isFalse();
        // 重复点击照样成功，只是标明是重复。
        assertThat(unsubscribes.unsubscribe(token, "trace").alreadyUnsubscribed()).isTrue();

        // 之后的任务把同一个地址放进名单，也不会再发给他。
        TaskView second = tasks.create(p.owner(), new NewTask(p.surveyId(), DeliveryChannel.EMAIL, null,
                "再邀请一次", DeliveryFixture.EMAIL_BODY,
                List.of(new NewRecipient(address, "用户2", null, Map.of())), null));
        email.reset();
        fixture.run(p.tenant(), second.id());

        assertThat(email.delivered()).isEmpty();
        assertThat(fixture.status(p.owner(), second.id()).skippedCount()).isEqualTo(1);
        assertThat(fixture.recipientViews(p.owner(), second.id()))
                .singleElement()
                .satisfies(recipient -> assertThat(recipient.state()).isEqualTo("unsubscribed"));
    }

    /** 预算用尽时任务退回待办，下一轮接着发，一个不落也不重复。 */
    @Test
    void aRoundWithASmallBudgetLeavesTheRestForTheNextRound() {
        TaskView task = fixture.emailTask(p, 4);

        assertThat(worker.sendPending(p.tenant(), task.id(), 2)).isEqualTo(2);
        assertThat(email.delivered()).hasSize(2);
        assertThat(fixture.status(p.owner(), task.id()).status()).isEqualTo("queued");

        fixture.run(p.tenant(), task.id());

        assertThat(email.delivered()).hasSize(4);
        assertThat(fixture.status(p.owner(), task.id()).status()).isEqualTo("completed");
    }

    @Test
    void aPermanentlyRejectedAddressFailsWithoutBlockingTheRest() {
        TaskView task = fixture.emailTask(p, 3);
        email.rejectAddress(fixture.addressOf(p.tenant(), task.id(), 2), "invalid_mailbox");

        fixture.run(p.tenant(), task.id());

        TaskView status = fixture.status(p.owner(), task.id());
        assertThat(status.sentCount()).isEqualTo(2);
        assertThat(status.failedCount()).isEqualTo(1);
    }

    @Test
    void aTemporaryFailureIsRetriedUntilItSucceeds() {
        TaskView task = fixture.emailTask(p, 1);
        email.failTemporarily(1);

        fixture.run(p.tenant(), task.id());
        fixture.run(p.tenant(), task.id());

        assertThat(email.delivered()).hasSize(1);
        assertThat(fixture.status(p.owner(), task.id()).sentCount()).isEqualTo(1);
    }

    @Test
    void theSmsChannelUsesMobileNumbersAndNeedsNoUnsubscribePlaceholder() {
        TaskView task = tasks.create(p.owner(), new NewTask(p.surveyId(), DeliveryChannel.SMS, null, null,
                DeliveryFixture.SMS_BODY,
                List.of(new NewRecipient("+86 138-0013-8000", "用户甲", null, Map.of())), null));

        fixture.run(p.tenant(), task.id());

        assertThat(sms.delivered()).singleElement()
                .satisfies(delivered -> assertThat(delivered.address()).isEqualTo("13800138000"));
    }

    @Test
    void recipientListsAreValidatedAtTheBoundaryAndErrorsNeverEchoTheAddress() {
        assertThatThrownBy(() -> tasks.create(p.owner(), new NewTask(p.surveyId(), DeliveryChannel.EMAIL, null,
                null, DeliveryFixture.EMAIL_BODY,
                List.of(NewRecipient.of("not-an-email")), null)))
                .isInstanceOf(InvalidDeliveryRequestException.class)
                .hasMessage("recipient 1 is not a valid email address");

        assertThatThrownBy(() -> tasks.create(p.owner(), new NewTask(p.surveyId(), DeliveryChannel.EMAIL, null,
                null, "没有链接占位符", List.of(NewRecipient.of("a@example.com")), null)))
                .isInstanceOf(InvalidDeliveryRequestException.class)
                .hasMessageContaining(MessageRenderer.LINK);

        assertThatThrownBy(() -> tasks.create(p.owner(), new NewTask(p.surveyId(), DeliveryChannel.EMAIL, null,
                null, "请填写：{{link}}", List.of(NewRecipient.of("a@example.com")), null)))
                .isInstanceOf(InvalidDeliveryRequestException.class)
                .hasMessageContaining(MessageRenderer.UNSUBSCRIBE);

        assertThatThrownBy(() -> tasks.create(p.owner(), new NewTask(p.surveyId(), DeliveryChannel.EMAIL, null,
                null, DeliveryFixture.EMAIL_BODY,
                List.of(new NewRecipient("a@example.com", null, "same-code", Map.of()),
                        new NewRecipient("b@example.com", null, "same-code", Map.of())), null)))
                .isInstanceOf(InvalidDeliveryRequestException.class)
                .hasMessage("recipient 2 reuses an invitation code");
    }

    @Test
    void recipientProgressShowsRedactedAddressesOnly() {
        TaskView task = fixture.emailTask(p, 1);

        assertThat(fixture.recipientViews(p.owner(), task.id()))
                .singleElement()
                .satisfies(recipient -> assertThat(recipient.address())
                        .doesNotContain("user1")
                        .contains("@example.com"));
    }

    @Test
    void theSameIdempotencyKeyDoesNotCreateASecondTask() {
        NewTask request = new NewTask(p.surveyId(), DeliveryChannel.EMAIL, null, "请填写问卷",
                DeliveryFixture.EMAIL_BODY, fixture.recipients(2), "batch-1");

        TaskView first = tasks.create(p.owner(), request);
        TaskView again = tasks.create(p.owner(), request);

        assertThat(again.id()).isEqualTo(first.id());
    }

    /** 与邮件正文里那条退订地址用的是同一个令牌签发器，因此这就是收件人真正会点到的令牌。 */
    private String unsubscribeToken(Published published, UUID taskId, UUID recipientId) {
        String token = tokens.issue(published.tenant(), taskId, recipientId);
        assertThat(email.calls().stream().map(DeliveryMessage::body))
                .anySatisfy(body -> assertThat(body).contains(UnsubscribeService.PATH + token));
        return token;
    }
}
