package cn.mjy.platform.survey;

import static org.assertj.core.api.Assertions.assertThat;

import cn.mjy.platform.survey.SurveyFixture.Workspace;
import java.util.ArrayList;
import java.util.List;
import java.util.UUID;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.TimeUnit;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;

/** 同一问卷同一时刻只允许一个发布：行锁把并发请求串行化，第二个看到 publishing 即得 409。 */
@SpringBootTest
class SurveyPublishConcurrencyTest {

    private static final int CONTENDERS = 4;

    @Autowired
    private SurveyFixture fixture;

    @Autowired
    private SurveyPublishService publisher;

    @Autowired
    private FakePublishGateway gateway;

    @Test
    void concurrentPublishesOfTheSameSurveyReachTheGatewayExactlyOnce() throws Exception {
        Workspace ws = fixture.workspace();
        UUID survey = fixture.newSurvey(ws).id();
        gateway.hold(survey);
        CountDownLatch start = new CountDownLatch(1);
        List<CompletableFuture<String>> results = new ArrayList<>();
        try (ExecutorService pool = Executors.newFixedThreadPool(CONTENDERS)) {
            for (int i = 0; i < CONTENDERS; i++) {
                results.add(CompletableFuture.supplyAsync(() -> attempt(start, ws, survey), pool));
            }
            start.countDown();
            assertThat(gateway.awaitEntered(survey, 10)).isTrue();
            // 胜出者停在网关里；其余请求必须在它返回之前就被拒绝。
            long deadline = System.nanoTime() + TimeUnit.SECONDS.toNanos(20);
            while (results.stream().filter(CompletableFuture::isDone).count() < CONTENDERS - 1
                    && System.nanoTime() < deadline) {
                TimeUnit.MILLISECONDS.sleep(20);
            }
            assertThat(gateway.callsFor(survey)).hasSize(1);
            gateway.release(survey);
        } finally {
            gateway.release(survey);
        }

        List<String> outcomes = results.stream().map(CompletableFuture::join).toList();
        assertThat(outcomes).filteredOn("published"::equals).hasSize(1);
        assertThat(outcomes).filteredOn("publish_in_progress"::equals).hasSize(CONTENDERS - 1);
        assertThat(gateway.callsFor(survey)).hasSize(1);
    }

    private String attempt(CountDownLatch start, Workspace ws, UUID survey) {
        try {
            start.await();
            return publisher.publish(ws.owner(), survey).survey().status().code();
        } catch (SurveyConflictException e) {
            return e.code();
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            return "interrupted";
        }
    }
}
