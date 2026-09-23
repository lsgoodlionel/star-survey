package cn.mjy.platform.delivery;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import cn.mjy.platform.engine.EngineEventType;
import cn.mjy.platform.response.ResponseFixture;
import cn.mjy.platform.response.ResponseFixture.Published;
import cn.mjy.platform.shared.TenantId;
import cn.mjy.platform.shared.tenant.TenantScope;
import java.time.Duration;
import java.util.List;
import java.util.UUID;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.jdbc.core.simple.JdbcClient;

/**
 * 催答（R05-06）：已完成的人不再被催、催答次数有上限、完成与催答的竞态不会造成重复提醒。
 */
@DeliveryIntegrationTest
class DeliveryReminderTest {

    private static final int RECIPIENTS = 4;
    private static final Duration FIRST_DELAY = Duration.ofHours(24);
    private static final Duration INTERVAL = Duration.ofHours(24);

    @Autowired
    private DeliveryFixture fixture;

    @Autowired
    private ReminderService reminders;

    @Autowired
    private CompletionService completions;

    @Autowired
    private CompletionReconciler reconciler;

    @Autowired
    private FakeRespondentIdentityResolver identities;

    @Autowired
    private FakeEmailProvider email;

    @Autowired
    private TenantScope tenantScope;

    @Autowired
    private JdbcClient jdbc;

    private Published p;
    private TaskView invite;

    @BeforeEach
    void anInvitationThatWentOut() {
        email.reset();
        identities.reset();
        p = fixture.publishedSurvey();
        invite = fixture.emailTask(p, RECIPIENTS);
        fixture.run(p.tenant(), invite.id());
        email.reset();
    }

    private ReminderRuleView rule(int maxReminders) {
        return reminders.create(p.owner(), p.surveyId(), new ReminderService.NewRule(invite.id(),
                FIRST_DELAY, INTERVAL, maxReminders, "还没填完", DeliveryFixture.REMINDER_BODY));
    }

    @Test
    void nobodyIsRemindedBeforeTheConfiguredDelay() {
        rule(3);

        assertThat(reminders.runOnce(p.tenant())).isZero();
        assertThat(fixture.reminderTasks(p.tenant(), invite.id())).isEmpty();
    }

    @Test
    void everyoneWhoHasNotCompletedIsRemindedOnce() {
        rule(3);
        fixture.ageSentAt(p.tenant(), invite.id(), FIRST_DELAY.plusHours(1));

        assertThat(reminders.runOnce(p.tenant())).isEqualTo(RECIPIENTS);
        UUID reminderTask = fixture.reminderTasks(p.tenant(), invite.id()).get(0);
        fixture.run(p.tenant(), reminderTask);

        assertThat(email.delivered()).hasSize(RECIPIENTS);
    }

    @Test
    void someoneWhoAlreadyCompletedIsNotReminded() {
        rule(3);
        String completed = fixture.respondentKeyOf(p.tenant(), invite.id(), 2);
        completions.record(p.owner(), p.surveyId(), completed, null);
        fixture.ageSentAt(p.tenant(), invite.id(), FIRST_DELAY.plusHours(1));

        assertThat(reminders.runOnce(p.tenant())).isEqualTo(RECIPIENTS - 1);

        UUID reminderTask = fixture.reminderTasks(p.tenant(), invite.id()).get(0);
        fixture.run(p.tenant(), reminderTask);
        assertThat(email.delivered()).hasSize(RECIPIENTS - 1);
        assertThat(email.deliveredTo(fixture.addressOf(p.tenant(), invite.id(), 2))).isEmpty();
    }

    /**
     * 竞态：催答已经生成（生成事务已提交），完成记录在<b>发送之前</b>才落库。
     * 发送关会再查一次完成记录，因此这条催答不会被发出去。
     */
    @Test
    void aCompletionThatLandsBetweenSelectionAndSendingStopsTheReminder() {
        rule(3);
        fixture.ageSentAt(p.tenant(), invite.id(), FIRST_DELAY.plusHours(1));
        assertThat(reminders.runOnce(p.tenant())).isEqualTo(RECIPIENTS);
        UUID reminderTask = fixture.reminderTasks(p.tenant(), invite.id()).get(0);

        // 催答任务已经排好队，此刻这个人交卷了。
        String raced = fixture.respondentKeyOf(p.tenant(), invite.id(), 3);
        completions.record(p.owner(), p.surveyId(), raced, null);

        fixture.run(p.tenant(), reminderTask);

        assertThat(email.delivered()).hasSize(RECIPIENTS - 1);
        assertThat(email.deliveredTo(fixture.addressOf(p.tenant(), invite.id(), 3))).isEmpty();
        assertThat(fixture.status(p.owner(), reminderTask).skippedCount()).isEqualTo(1);
    }

    /**
     * 更贴近验收原话的竞态：催答已经<b>认领</b>（发送台账已写、收件人停在 sending，进程此刻崩了），
     * 完成记录随后才落库。恢复时的重新认领会在收件人行锁下再查一次完成记录，因此这条催答仍然不会发出去。
     */
    @Test
    void aCompletionThatLandsWhileASendIsAlreadyClaimedStillStopsTheReminder() {
        rule(3);
        fixture.ageSentAt(p.tenant(), invite.id(), FIRST_DELAY.plusHours(1));
        reminders.runOnce(p.tenant());
        UUID reminderTask = fixture.reminderTasks(p.tenant(), invite.id()).get(0);

        // 第一个收件人认领之后，进程在调渠道商的途中死掉。
        email.crashAfter(0);
        assertThatThrownBy(() -> fixture.run(p.tenant(), reminderTask))
                .isInstanceOf(FakeDeliveryProvider.SimulatedCrash.class);
        List<String> claimed = claimedRespondentKeys(p.tenant(), reminderTask);
        assertThat(claimed).hasSize(1);

        // 正是这个被认领、结果未知的人交了卷。
        completions.record(p.owner(), p.surveyId(), claimed.get(0), null);

        email.stopCrashing();
        fixture.run(p.tenant(), reminderTask);

        assertThat(email.delivered()).hasSize(RECIPIENTS - 1);
        assertThat(fixture.status(p.owner(), reminderTask).skippedCount()).isEqualTo(1);
    }

    /** 同一轮里重复调用不会把同一个人催两次：催答计数与生成在同一事务、同一把行锁下。 */
    @Test
    void runningTheReminderRoundTwiceDoesNotRemindAnyoneTwice() {
        rule(3);
        fixture.ageSentAt(p.tenant(), invite.id(), FIRST_DELAY.plusHours(1));

        assertThat(reminders.runOnce(p.tenant())).isEqualTo(RECIPIENTS);
        assertThat(reminders.runOnce(p.tenant())).isZero();

        assertThat(remindersSent(p.tenant(), invite.id())).containsOnly(1);
    }

    @Test
    void theReminderCapIsRespected() {
        rule(2);

        for (int round = 1; round <= 5; round++) {
            fixture.ageSentAt(p.tenant(), invite.id(), INTERVAL.plusHours(1));
            reminders.runOnce(p.tenant());
        }
        for (UUID reminderTask : fixture.reminderTasks(p.tenant(), invite.id())) {
            fixture.run(p.tenant(), reminderTask);
        }

        assertThat(remindersSent(p.tenant(), invite.id())).containsOnly(2);
        assertThat(email.delivered()).hasSize(RECIPIENTS * 2);
    }

    @Test
    void anUnsubscribedRecipientIsNeverReminded() {
        rule(3);
        String address = fixture.addressOf(p.tenant(), invite.id(), 1);
        tenantScope.run(p.tenant(), () -> jdbc.sql("""
                        INSERT INTO delivery_unsubscribe (tenant_id, channel, address, reason)
                        VALUES (:tenant, 'email', :address, 'self')
                        """)
                .param("tenant", p.tenant().value())
                .param("address", address)
                .update());
        fixture.ageSentAt(p.tenant(), invite.id(), FIRST_DELAY.plusHours(1));

        assertThat(reminders.runOnce(p.tenant())).isEqualTo(RECIPIENTS - 1);
    }

    /** 引擎投影里新出现的已完成答卷经对账变成完成记录，催答据此跳过这个人。 */
    @Test
    void completionsReconciledFromTheEngineProjectionAlsoStopReminders() {
        rule(3);
        String key = fixture.respondentKeyOf(p.tenant(), invite.id(), 4);
        fixture.responses().response(p.tenant(), p.instance(), p.sid(), ResponseFixture.GENERATION, 77L,
                EngineEventType.COMPLETED);
        identities.map(p.tenant(), p.instance(), p.sid(), ResponseFixture.GENERATION, 77L, key);

        assertThat(reconciler.runOnce(p.tenant())).isEqualTo(1);

        fixture.ageSentAt(p.tenant(), invite.id(), FIRST_DELAY.plusHours(1));
        assertThat(reminders.runOnce(p.tenant())).isEqualTo(RECIPIENTS - 1);
    }

    @Test
    void aSurveyCanOnlyHaveOneReminderRulePerInvitation() {
        rule(3);

        assertThatThrownBy(() -> rule(3))
                .isInstanceOf(DeliveryConflictException.class)
                .extracting(e -> ((DeliveryConflictException) e).code())
                .isEqualTo("reminder_rule_exists");
    }

    @Test
    void aDisabledRuleStopsGeneratingReminders() {
        ReminderRuleView created = rule(3);
        reminders.setEnabled(p.owner(), created.id(), false);
        fixture.ageSentAt(p.tenant(), invite.id(), FIRST_DELAY.plusHours(1));

        assertThat(reminders.runOnce(p.tenant())).isZero();
    }

    /** 已经写进发送台账、但还停在 sending 的收件人的应答者标识。 */
    private List<String> claimedRespondentKeys(TenantId tenant, UUID taskId) {
        return tenantScope.call(tenant, () -> jdbc.sql("""
                        SELECT r.respondent_key FROM delivery_recipient r
                          JOIN delivery_send s ON s.task_id = r.task_id AND s.recipient_id = r.id
                         WHERE r.task_id = :task AND r.state = 'sending'
                        """)
                .param("task", taskId)
                .query(String.class)
                .list());
    }

    private List<Integer> remindersSent(TenantId tenant, UUID taskId) {
        return tenantScope.call(tenant, () -> jdbc.sql("""
                        SELECT reminders_sent FROM delivery_recipient WHERE task_id = :task ORDER BY id
                        """)
                .param("task", taskId)
                .query(Integer.class)
                .list());
    }
}
