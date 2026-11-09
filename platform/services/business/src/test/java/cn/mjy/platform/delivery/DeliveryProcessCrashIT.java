package cn.mjy.platform.delivery;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.doAnswer;

import cn.mjy.platform.response.ResponseFixture.Published;
import cn.mjy.platform.shared.TenantId;
import cn.mjy.platform.shared.tenant.TenantScope;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.UUID;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.condition.EnabledIfEnvironmentVariable;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.test.context.bean.override.mockito.MockitoSpyBean;

/** Explicit disposable-process fixture. Controller kills phase one after fake acceptance. */
@SpringBootTest(properties = {
        "PLATFORM_DELIVERY_SECRET=" + DeliveryIntegrationTest.MASTER_SECRET,
        "platform.delivery.public-base-url=https://survey.example",
        "platform.delivery.rate-window=PT1S", "platform.delivery.send-lease=PT10M",
        "platform.delivery.enabled=false", "platform.delivery.email.enabled=false"
})
@EnabledIfEnvironmentVariable(named="SURVEY_PROCESS_CRASH_FIXTURE", matches="/fixture")
class DeliveryProcessCrashIT {
    private static final Path DIR = Path.of("/fixture");
    @Autowired DeliveryFixture fixture;
    @Autowired DeliveryWorker worker;
    @Autowired TenantScope scope;
    @Autowired JdbcClient jdbc;
    @Autowired DeliveryProviders providers;
    @MockitoSpyBean FakeEmailProvider email;

    @Test
    void killedAfterAcceptanceRecoversWithStableKeyAndLease() throws Exception {
        String phase = System.getenv("SURVEY_PROCESS_CRASH_PHASE");
        assertThat(providers.require(DeliveryChannel.EMAIL)).isSameAs(email);
        if ("first".equals(phase)) {
            Published published = fixture.publishedSurvey();
            TaskView task = fixture.emailTask(published, 1);
            Files.writeString(DIR.resolve("identity"), published.tenant() + "\n" + task.id() + "\n");
            doAnswer(call -> {
                DeliveryMessage message = call.getArgument(0);
                Files.writeString(DIR.resolve("captured-key"), message.idempotencyKey());
                Files.writeString(DIR.resolve("accepted"), "accepted");
                Thread.sleep(180_000); // host terminates the whole disposable container here
                throw new AssertionError("fixture controller failed to kill the process");
            }).when(email).send(any());
            fixture.run(published.tenant(), task.id());
            throw new AssertionError("fixture process unexpectedly survived");
        }
        assertThat(phase).isEqualTo("second");
        var identity = Files.readAllLines(DIR.resolve("identity"));
        TenantId tenant = TenantId.of(identity.get(0));
        UUID task = UUID.fromString(identity.get(1));
        String capturedKey = Files.readString(DIR.resolve("captured-key"));
        assertThat(scope.call(tenant, () -> jdbc.sql("SELECT state FROM delivery_recipient WHERE task_id=:task")
                .param("task", task).query(String.class).single())).isEqualTo("sending");
        doAnswer(call -> {
            DeliveryMessage message = call.getArgument(0);
            assertThat(message.idempotencyKey()).isEqualTo(capturedKey);
            Files.writeString(DIR.resolve("retry"), "same-key");
            return SendResult.accepted("synthetic-acceptance");
        }).when(email).send(any());
        // A live lease must not be reclaimed, even after the process restarts.
        assertThat(worker.sendPending(tenant, task, 1)).isZero();
        assertThat(Files.exists(DIR.resolve("retry"))).isFalse();
        scope.run(tenant, () -> jdbc.sql("UPDATE delivery_recipient SET updated_at=now()-interval '11 minutes' WHERE task_id=:task")
                .param("task", task).update());
        assertThat(worker.sendPending(tenant, task, 1)).isEqualTo(1);
        assertThat(Files.readString(DIR.resolve("retry"))).isEqualTo("same-key");
        assertThat(scope.call(tenant, () -> jdbc.sql("SELECT state FROM delivery_recipient WHERE task_id=:task")
                .param("task", task).query(String.class).single())).isEqualTo("sent");
        assertThat(scope.call(tenant, () -> jdbc.sql("SELECT count(*) FROM delivery_send WHERE task_id=:task")
                .param("task", task).query(Long.class).single())).isEqualTo(1);
        assertThat(scope.call(tenant, () -> jdbc.sql("SELECT count(*) FROM audit_log WHERE action='delivery.send'")
                .query(Long.class).single())).isEqualTo(1);
    }
}
