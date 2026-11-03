package cn.mjy.platform.delivery;

import static org.assertj.core.api.Assertions.assertThat;

import cn.mjy.platform.response.ResponseFixture.Published;
import java.time.Clock;
import java.time.Instant;
import java.time.ZoneOffset;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.test.context.TestPropertySource;
import org.springframework.test.util.ReflectionTestUtils;

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

    @Autowired
    private RateLimiter rateLimiter;

    private Clock originalClock;
    private Instant window = Instant.parse("2030-01-01T00:00:00Z");
    private Published p;

    @BeforeEach
    void publishedSurvey() {
        email.reset();
        originalClock = (Clock) ReflectionTestUtils.getField(rateLimiter, "clock");
        ReflectionTestUtils.setField(rateLimiter, "clock", Clock.fixed(window, ZoneOffset.UTC));
        p = fixture.publishedSurvey();
    }

    @AfterEach
    void restoreClock() {
        ReflectionTestUtils.setField(rateLimiter, "clock", originalClock);
    }

    @Test
    void theRoundStopsAtTheTenantLimitAndResumesInTheNextWindow() {
        TaskView task = fixture.emailTask(p, 5);

        worker.sendPending(p.tenant(), task.id(), 1000);

        assertThat(email.delivered()).hasSize(2);
        assertThat(fixture.status(p.owner(), task.id()).status()).isEqualTo("queued");

        // 明确推进固定窗口；被限速挡下的收件人仍在待发。
        advanceWindow();
        worker.sendPending(p.tenant(), task.id(), 1000);
        advanceWindow();
        worker.sendPending(p.tenant(), task.id(), 1000);
        advanceWindow();
        worker.sendPending(p.tenant(), task.id(), 1000);

        assertThat(email.delivered()).hasSize(5);
        assertThat(fixture.status(p.owner(), task.id()).sentCount()).isEqualTo(5);
        assertThat(fixture.status(p.owner(), task.id()).status()).isEqualTo("completed");
    }

    private void advanceWindow() {
        window = window.plusSeconds(1);
        ReflectionTestUtils.setField(rateLimiter, "clock", Clock.fixed(window, ZoneOffset.UTC));
    }
}
