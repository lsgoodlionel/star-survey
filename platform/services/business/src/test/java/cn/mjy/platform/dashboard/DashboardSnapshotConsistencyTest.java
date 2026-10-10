package cn.mjy.platform.dashboard;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyInt;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.Mockito.doAnswer;

import cn.mjy.platform.shared.tenant.TenantScope;
import cn.mjy.platform.survey.SurveyFixture;
import cn.mjy.platform.survey.SurveyFixture.Workspace;
import cn.mjy.platform.survey.SurveyView;
import java.lang.reflect.Method;
import java.time.Instant;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.test.context.bean.override.mockito.MockitoSpyBean;
import org.springframework.transaction.annotation.Isolation;
import org.springframework.transaction.annotation.Transactional;

@SpringBootTest
class DashboardSnapshotConsistencyTest {

    @Autowired
    private DashboardService dashboard;

    @Autowired
    private SurveyFixture fixture;

    @MockitoSpyBean
    private DashboardRecentWorkRepository recentWork;

    private Workspace workspace;
    private SurveyView survey;

    @BeforeEach
    void setUp() {
        workspace = fixture.workspace();
        survey = fixture.newSurvey(workspace);
    }

    @Test
    void dashboardReadIsDeclaredReadOnlyAndRepeatableRead() throws Exception {
        Method method = DashboardService.class.getMethod(
                "getDashboard", cn.mjy.platform.shared.TenantContext.class, int.class, int.class);

        Transactional transaction = method.getAnnotation(Transactional.class);

        assertThat(transaction).isNotNull();
        assertThat(transaction.readOnly()).isTrue();
        assertThat(transaction.isolation()).isEqualTo(Isolation.REPEATABLE_READ);
    }

    @Test
    void recentWorkAndAggregateComeFromTheSameLogicalSnapshot() throws Exception {
        CountDownLatch aggregateRead = new CountDownLatch(1);
        CountDownLatch continueRead = new CountDownLatch(1);
        String readerThread = "dashboard-snapshot-reader";
        doAnswer(invocation -> {
            if (Thread.currentThread().getName().equals(readerThread)) {
                aggregateRead.countDown();
                assertThat(continueRead.await(10, TimeUnit.SECONDS)).isTrue();
            }
            return invocation.callRealMethod();
        }).when(recentWork).findVisible(any(), anyString(), anyInt());

        CompletableFuture<DashboardView> read = CompletableFuture.supplyAsync(
                () -> dashboard.getDashboard(workspace.owner(), 20, 20),
                runnable -> {
                    Thread thread = new Thread(runnable, readerThread);
                    thread.start();
                });

        assertThat(aggregateRead.await(10, TimeUnit.SECONDS)).isTrue();
        assertThat(recentWork.upsert(workspace.tenant(), workspace.owner().actorId(),
                new RecentWorkCommand(survey.id(), DashboardPage.EDIT, null),
                Instant.parse("2026-10-10T12:00:00Z"))).isTrue();
        continueRead.countDown();

        DashboardView snapshot = read.get(10, TimeUnit.SECONDS);
        assertThat(snapshot.recentWork()).isEmpty();
        assertThat(dashboard.getDashboard(workspace.owner(), 20, 20).recentWork()).hasSize(1);
    }
}
