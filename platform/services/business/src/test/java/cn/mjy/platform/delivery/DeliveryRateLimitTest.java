package cn.mjy.platform.delivery;

import static org.assertj.core.api.Assertions.assertThat;

import cn.mjy.platform.response.ResponseFixture.Published;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.test.context.TestPropertySource;

/** 每租户限速：一个窗口内发满就停，任务退回待办，下一个窗口接着发，总量不多不少。 */
@DeliveryIntegrationTest
@TestPropertySource(properties = {
        "platform.delivery.rate-per-window=2",
        "platform.delivery.rate-window=PT1S"
})
class DeliveryRateLimitTest {

    @Autowired
    private DeliveryFixture fixture;

    @Autowired
    private DeliveryWorker worker;

    @Autowired
    private FakeEmailProvider email;

    private Published p;

    @BeforeEach
    void publishedSurvey() {
        email.reset();
        p = fixture.publishedSurvey();
    }

    @Test
    void theRoundStopsAtTheTenantLimitAndResumesInTheNextWindow() {
        TaskView task = fixture.emailTask(p, 5);

        worker.sendPending(p.tenant(), task.id(), 1000);

        assertThat(email.delivered()).hasSize(2);
        assertThat(fixture.status(p.owner(), task.id()).status()).isEqualTo("queued");

        // 等窗口翻页；上一轮被限速挡下的收件人没有被标成失败，仍在待发。
        sleepPastWindow();
        worker.sendPending(p.tenant(), task.id(), 1000);
        sleepPastWindow();
        worker.sendPending(p.tenant(), task.id(), 1000);
        sleepPastWindow();
        worker.sendPending(p.tenant(), task.id(), 1000);

        assertThat(email.delivered()).hasSize(5);
        assertThat(fixture.status(p.owner(), task.id()).sentCount()).isEqualTo(5);
        assertThat(fixture.status(p.owner(), task.id()).status()).isEqualTo("completed");
    }

    private static void sleepPastWindow() {
        try {
            Thread.sleep(1100);
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
        }
    }
}
