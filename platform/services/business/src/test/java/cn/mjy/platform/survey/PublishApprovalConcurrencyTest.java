package cn.mjy.platform.survey;

import static org.assertj.core.api.Assertions.assertThat;

import cn.mjy.platform.shared.TenantContext;
import cn.mjy.platform.shared.tenant.TenantScope;
import cn.mjy.platform.survey.SurveyFixture.Workspace;
import cn.mjy.platform.survey.gateway.GatewayRequest;
import java.util.UUID;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.TimeUnit;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.jdbc.core.simple.JdbcClient;

/**
 * 批准与改稿、发布与改稿的竞态：问卷行锁把它们串行化，任何交错都不可能发布一个未经批准的草稿版本。
 */
@SpringBootTest
class PublishApprovalConcurrencyTest {

    private static final int RACE_ROUNDS = 8;
    private static final String APPROVED_TITLE = "已批准的版本";

    @Autowired
    private SurveyFixture fixture;

    @Autowired
    private SurveyService surveys;

    @Autowired
    private SurveyPublishService publisher;

    @Autowired
    private PublishApprovalService approvals;

    @Autowired
    private FakePublishGateway gateway;

    @Autowired
    private TenantScope tenantScope;

    @Autowired
    private JdbcClient jdbc;

    private Workspace ws;
    private TenantContext editor;
    private TenantContext reviewer;

    @BeforeEach
    void aTenantThatRequiresApproval() {
        ws = fixture.workspace();
        fixture.requirePublishApproval(ws, true);
        editor = fixture.member(ws, "sub-editor", "editor", ws.project());
        reviewer = fixture.member(ws, "reviewer", "publish_reviewer", ws.project());
    }

    private int currentDraftVersion(UUID survey) {
        return surveys.draft(ws.owner(), survey).version();
    }

    private void saveDraft(UUID survey, String title) {
        surveys.saveDraft(editor, survey, currentDraftVersion(survey), fixture.definitionTitled(title));
    }

    /** 等到有会话在等锁（被测线程已经卡在行锁上）。 */
    private void awaitSomeoneWaitingForALock() throws InterruptedException {
        long deadline = System.nanoTime() + TimeUnit.SECONDS.toNanos(10);
        while (jdbc.sql("""
                SELECT COUNT(*) FROM pg_stat_activity
                 WHERE datname = current_database() AND wait_event_type = 'Lock'
                """).query(Long.class).single() == 0) {
            assertThat(System.nanoTime()).as("a session should be waiting for the survey row lock")
                    .isLessThan(deadline);
            TimeUnit.MILLISECONDS.sleep(10);
        }
    }

    @Test
    void aDraftSaveHoldingTheSurveyLockVoidsThePendingRequestBeforeTheApproverCanApproveIt() throws Exception {
        UUID survey = fixture.newSurvey(ws).id();
        UUID request = approvals.submit(editor, survey, 1).id();
        CompletableFuture<String> approval = new CompletableFuture<>();

        tenantScope.run(ws.tenant(), () -> {
            jdbc.sql("SELECT id FROM survey WHERE id = :id FOR UPDATE").param("id", survey).query(UUID.class).single();
            Thread.ofVirtual().start(() -> approval.complete(tryApprove(request)));
            try {
                awaitSomeoneWaitingForALock();
            } catch (InterruptedException e) {
                Thread.currentThread().interrupt();
                throw new IllegalStateException(e);
            }
            // 与持锁事务同一事务：改稿在批准者拿到锁之前提交。
            saveDraft(survey, "持锁期间改稿");
        });

        assertThat(approval.get(20, TimeUnit.SECONDS)).isEqualTo("approval_not_pending");
        assertThat(approvals.get(editor, request).request().status()).isEqualTo(PublishApprovalStatus.VOIDED);
        assertThat(tryPublish(survey)).isEqualTo("approval_required");
        assertThat(gateway.callsFor(survey)).isEmpty();
    }

    @Test
    void aDraftSaveDuringAnApprovedPublishCannotChangeWhatIsPublished() throws Exception {
        UUID survey = fixture.newSurvey(ws).id();
        saveDraft(survey, APPROVED_TITLE);
        int approvedVersion = currentDraftVersion(survey);
        UUID request = approvals.submit(editor, survey, approvedVersion).id();
        approvals.approve(reviewer, request);
        gateway.hold(survey);
        CompletableFuture<PublishOutcome> publish = CompletableFuture.supplyAsync(
                () -> publisher.publish(editor, survey));
        try {
            assertThat(gateway.awaitEntered(survey, 10)).isTrue();
            saveDraft(survey, "发布进行中改稿");
        } finally {
            gateway.release(survey);
        }

        PublishOutcome outcome = publish.get(20, TimeUnit.SECONDS);
        assertThat(outcome.survey().status()).isEqualTo(SurveyStatus.PUBLISHED);
        assertThat(outcome.version().draftVersion()).isEqualTo(approvedVersion);
        GatewayRequest sent = gateway.callsFor(survey).getFirst();
        assertThat(fixture.parse(sent.definitionJson()).get("title").asString()).isEqualTo(APPROVED_TITLE);
    }

    @Test
    void racingApprovalsAndDraftSavesNeverPublishAnUnapprovedVersion() throws Exception {
        try (ExecutorService pool = Executors.newFixedThreadPool(2)) {
            for (int round = 0; round < RACE_ROUNDS; round++) {
                UUID survey = fixture.newSurvey(ws).id();
                UUID request = approvals.submit(editor, survey, 1).id();
                CountDownLatch start = new CountDownLatch(1);
                CompletableFuture<String> approve = CompletableFuture.supplyAsync(
                        () -> awaitThen(start, () -> tryApprove(request)), pool);
                CompletableFuture<String> save = CompletableFuture.supplyAsync(
                        () -> awaitThen(start, () -> {
                            saveDraft(survey, "竞态改稿");
                            return "saved";
                        }), pool);
                start.countDown();
                approve.get(20, TimeUnit.SECONDS);
                save.get(20, TimeUnit.SECONDS);

                assertThat(staleApprovals(survey)).as("round " + round).isZero();
                String published = tryPublish(survey);
                if (published.equals("published")) {
                    int approvedVersion = approvals.get(editor, request).request().draftVersion();
                    assertThat(surveys.versions(ws.owner(), survey).getFirst().draftVersion())
                            .isEqualTo(approvedVersion);
                } else {
                    assertThat(published).as("round " + round).isEqualTo("approval_required");
                }
            }
        }
    }

    /** 状态仍为 approved、却对应旧草稿版本的申请数；任何时刻都必须为 0。 */
    private long staleApprovals(UUID survey) {
        return tenantScope.call(ws.tenant(), () -> jdbc.sql("""
                        SELECT COUNT(*) FROM survey_publish_approval a JOIN survey s ON s.id = a.survey_id
                         WHERE a.survey_id = :id AND a.status = 'approved' AND a.draft_version <> s.draft_version
                        """)
                .param("id", survey).query(Long.class).single());
    }

    private String tryApprove(UUID request) {
        try {
            return approvals.approve(reviewer, request).status().code();
        } catch (SurveyConflictException e) {
            return e.code();
        }
    }

    private String tryPublish(UUID survey) {
        try {
            return publisher.publish(editor, survey).survey().status().code();
        } catch (SurveyAccessDeniedException e) {
            return e.reason().name().toLowerCase(java.util.Locale.ROOT);
        }
    }

    private static <T> T awaitThen(CountDownLatch start, java.util.function.Supplier<T> work) {
        try {
            start.await();
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            throw new IllegalStateException(e);
        }
        return work.get();
    }
}
