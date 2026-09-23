package cn.mjy.platform.delivery;

import static org.assertj.core.api.Assertions.assertThat;

import cn.mjy.platform.access.MemberService;
import cn.mjy.platform.engine.EngineEventType;
import cn.mjy.platform.response.ResponseFixture;
import cn.mjy.platform.response.ResponseFixture.Published;
import cn.mjy.platform.shared.TenantContext;
import java.time.Duration;
import java.util.UUID;
import java.util.concurrent.atomic.AtomicLong;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;

/** 新答卷通知（R05-09）：只在有新增时触发一次、合并成一条、字段最小化、受收件人权限限制。 */
@DeliveryIntegrationTest
class DeliveryNotificationTest {

    private static final AtomicLong NEXT_RESPONSE_ID = new AtomicLong(1_000);

    @Autowired
    private DeliveryFixture fixture;

    @Autowired
    private NotificationService notifications;

    @Autowired
    private MemberService members;

    @Autowired
    private FakeEmailProvider email;

    private Published p;
    private TenantContext analyst;

    @BeforeEach
    void aSurveyWithAnAnalyst() {
        email.reset();
        p = fixture.publishedSurvey();
        analyst = fixture.responses().member(p, "analyst", "statistics_viewer");
    }

    private NotificationRuleView rule() {
        return notifications.create(p.owner(), p.surveyId(), new NotificationService.NewRule(
                analyst.actorId(), DeliveryChannel.EMAIL, "analyst@example.com", Duration.ofSeconds(60)));
    }

    @Test
    void nothingIsSentWhileThereAreNoNewResponses() {
        rule();

        assertThat(notifications.runOnce(p.tenant())).isZero();
    }

    @Test
    void severalNewResponsesAreMergedIntoOneMessageWithMinimalFields() {
        rule();
        completeResponses(3);

        assertThat(notifications.runOnce(p.tenant())).isEqualTo(1);
        sendPendingNotifications();

        assertThat(email.delivered()).singleElement().satisfies(delivered -> {
            assertThat(delivered.address()).isEqualTo("analyst@example.com");
            assertThat(delivered.body()).contains("新增 3 份答卷").contains(p.surveyId().toString());
            // 字段最小化：通知里没有任何作答内容。
            assertThat(delivered.body()).doesNotContain("Q100").doesNotContain("Q101");
        });
    }

    @Test
    void theSameResponsesNeverTriggerASecondNotification() {
        rule();
        completeResponses(2);

        assertThat(notifications.runOnce(p.tenant())).isEqualTo(1);
        assertThat(notifications.runOnce(p.tenant())).isZero();
    }

    @Test
    void aMemberWhoLostAccessStopsReceivingNotifications() {
        rule();
        completeResponses(1);
        members.remove(p.owner(), analyst.actorId());

        assertThat(notifications.runOnce(p.tenant())).isZero();
        assertThat(email.delivered()).isEmpty();
    }

    @Test
    void aDisabledRuleSendsNothing() {
        NotificationRuleView created = rule();
        notifications.setEnabled(p.owner(), created.id(), false);
        completeResponses(2);

        assertThat(notifications.runOnce(p.tenant())).isZero();
    }

    @Test
    void theRuleViewRedactsTheAddress() {
        assertThat(rule().address()).isEqualTo("a***@example.com");
    }

    private void completeResponses(int count) {
        for (int i = 0; i < count; i++) {
            fixture.responses().response(p.tenant(), p.instance(), p.sid(), ResponseFixture.GENERATION,
                    NEXT_RESPONSE_ID.incrementAndGet(), EngineEventType.COMPLETED);
        }
    }

    private void sendPendingNotifications() {
        for (UUID taskId : fixture.notificationTasks(p.tenant(), p.surveyId())) {
            fixture.run(p.tenant(), taskId);
        }
    }
}
