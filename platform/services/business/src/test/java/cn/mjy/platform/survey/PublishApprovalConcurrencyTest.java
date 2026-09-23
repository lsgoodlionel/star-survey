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
    private static final long LOCK_WAIT_TIMEOUT_SECONDS = 10;
    private static final long LOCK_WAIT_POLL_MILLIS = 10;

    /**
     * 有别的会话正卡在“本连接这笔事务”持有的问卷行锁上吗？
     *
     * <p>行锁的等待体现为：等待者要本事务 xid 上的 ShareLock（transactionid 锁），而本连接正持有它；
     * 再要求等待者同时持有 survey 表的锁，把范围钉死在问卷行上。
     */
    private static final String BLOCKED_ON_MY_TRANSACTION = """
            SELECT COUNT(*)
              FROM pg_locks waiting
              JOIN pg_locks held ON held.locktype = 'transactionid' AND held.granted
                                AND held.transactionid = waiting.transactionid
                                AND held.pid = pg_backend_pid()
              JOIN pg_locks on_survey ON on_survey.pid = waiting.pid AND on_survey.granted
                                AND on_survey.locktype = 'relation'
                                AND on_survey.relation = 'survey'::regclass
             WHERE NOT waiting.granted AND waiting.locktype = 'transactionid'
               AND waiting.pid <> pg_backend_pid()
            """;

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

    /**
     * 等到批准者那条会话真的卡在本事务持有的问卷行锁上。
     *
     * <p>不能用 pg_stat_activity 数“有谁在等锁”：后端状态快照按事务缓存，本方法在一笔长事务里轮询，
     * 第一次读到什么就一直是什么——之后才连上来阻塞的批准者永远不会出现（本地必然超时），
     * 而整套测试一起跑时，第一次就可能读到别的测试的等待会话，凭空满足条件（假阳性）。
     * pg_locks 每次都直读锁管理器，并且能钉死“等的就是本连接这笔事务”，不可能被无关会话满足。
     */
    private void awaitAnotherSessionBlockedOnThisTransaction(CompletableFuture<?> approver)
            throws InterruptedException {
        long deadline = System.nanoTime() + TimeUnit.SECONDS.toNanos(LOCK_WAIT_TIMEOUT_SECONDS);
        while (jdbc.sql(BLOCKED_ON_MY_TRANSACTION).query(Long.class).single() == 0) {
            assertThat(approver).as("the approver must block on the survey row lock, not decide without it")
                    .isNotDone();
            assertThat(System.nanoTime()).as("a session should be waiting for the survey row lock")
                    .isLessThan(deadline);
            TimeUnit.MILLISECONDS.sleep(LOCK_WAIT_POLL_MILLIS);
        }
    }

    @Test
    void aDraftSaveHoldingTheSurveyLockVoidsThePendingRequestBeforeTheApproverCanApproveIt() throws Exception {
        UUID survey = fixture.newSurvey(ws).id();
        UUID request = approvals.submit(editor, survey, 1).id();
        CompletableFuture<String> approval = new CompletableFuture<>();

        tenantScope.run(ws.tenant(), () -> {
            jdbc.sql("SELECT id FROM survey WHERE id = :id FOR UPDATE").param("id", survey).query(UUID.class).single();
            Thread.ofVirtual().start(() -> {
                try {
                    approval.complete(tryApprove(request));
                } catch (RuntimeException | Error e) {
                    // 否则批准者线程静默死掉，等待循环只会超时，看不出真正的原因。
                    approval.completeExceptionally(e);
                }
            });
            try {
                awaitAnotherSessionBlockedOnThisTransaction(approval);
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
