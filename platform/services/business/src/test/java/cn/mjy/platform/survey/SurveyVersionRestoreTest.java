package cn.mjy.platform.survey;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import cn.mjy.platform.access.DecisionReason;
import cn.mjy.platform.shared.TenantContext;
import cn.mjy.platform.survey.SurveyFixture.Workspace;
import cn.mjy.platform.tenant.routing.SurveyRoute;
import cn.mjy.platform.tenant.routing.SurveyRouteService;
import java.util.ArrayList;
import java.util.List;
import java.util.UUID;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;

/**
 * 旧版恢复（WP-01 01.4）：把某个历史发布版本的定义取回来当作当前草稿。
 *
 * <p>与 ADR 0012 的语义一致——恢复<b>只写草稿</b>：已发布版本仍不可变、公开路由仍指向原来的在线版本、
 * 引擎里的问卷一个都不动（不调网关）。想让旧内容重新上线，照常走"改稿→发布"，于是它成为一个
 * 全新的版本（新引擎问卷、新 sid），历史版本与它们的答卷原样保留。
 *
 * <p>并发保护沿用草稿的乐观锁：恢复必须带上 expectedVersion，与并发保存互斥。
 */
@SpringBootTest
class SurveyVersionRestoreTest {

    private static final int WRITERS = 5;

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
    private SurveyRouteService routes;

    private Workspace ws;
    private UUID survey;
    private PublishedVersionView v1;
    private PublishedVersionView v2;

    /** 第 1 版"初版"上线后改稿再发布第 2 版；此刻在线的是第 2 版，草稿版本为 2。 */
    @BeforeEach
    void twoPublishedVersions() {
        ws = fixture.workspace();
        survey = fixture.newSurvey(ws).id();
        v1 = publisher.publish(ws.owner(), survey).version();
        surveys.saveDraft(ws.owner(), survey, 1, fixture.definitionTitled("第二版"));
        v2 = publisher.publish(ws.owner(), survey).version();
    }

    private int draftVersion() {
        return surveys.draft(ws.owner(), survey).version();
    }

    private String draftTitle() {
        return surveys.draft(ws.owner(), survey).definition().get("title").asString();
    }

    private int currentRouteSid() {
        return routes.findByPublicId(ws.tenant(), survey).map(SurveyRoute::engineSid).orElseThrow();
    }

    @Test
    void restoringAnOldVersionReplacesTheDraftWithItsDefinitionAndBumpsTheDraftVersion() {
        DraftView restored = surveys.restore(ws.owner(), survey, 1, 2);

        assertThat(restored.version()).isEqualTo(3);
        assertThat(restored.definition()).isEqualTo(v1.definition());
        assertThat(draftTitle()).isEqualTo("P0-00.8 发布网关样例问卷");
        assertThat(surveys.get(ws.owner(), survey).title()).isEqualTo("P0-00.8 发布网关样例问卷");
    }

    @Test
    void restoringDoesNotTouchThePublishedVersionsThePublicRouteOrTheEngine() {
        int callsBefore = gateway.callsFor(survey).size();

        surveys.restore(ws.owner(), survey, 1, 2);

        // 公开路由仍指向第 2 版的引擎问卷，在线版本没变，引擎没有被再碰一次。
        assertThat(currentRouteSid()).isEqualTo(v2.engineSid());
        assertThat(surveys.get(ws.owner(), survey).publishedVersion()).isEqualTo(2);
        assertThat(gateway.callsFor(survey)).hasSize(callsBefore);
        assertThat(gateway.closeCallsFor(v2.engineSid())).isEmpty();

        // 两个历史版本逐字段不变，尤其是 sid 与在线标记。
        List<PublishedVersionView> versions = surveys.versions(ws.owner(), survey);
        assertThat(versions).extracting(PublishedVersionView::version).containsExactly(1, 2);
        assertThat(versions.get(0).definition()).isEqualTo(v1.definition());
        assertThat(versions.get(0).engineSid()).isEqualTo(v1.engineSid());
        assertThat(versions.get(0).live()).isFalse();
        assertThat(versions.get(1).definition()).isEqualTo(v2.definition());
        assertThat(versions.get(1).engineSid()).isEqualTo(v2.engineSid());
        assertThat(versions.get(1).live()).isTrue();
        assertThat(versions.get(1).supersededAt()).isNull();
    }

    @Test
    void publishingARestoredDraftMakesItANewVersionOnANewEngineSurveyAndLeavesTheOldOnesIntact() {
        surveys.restore(ws.owner(), survey, 1, 2);

        PublishedVersionView v3 = publisher.publish(ws.owner(), survey).version();

        assertThat(v3.version()).isEqualTo(3);
        assertThat(v3.draftVersion()).isEqualTo(3);
        assertThat(v3.definition().get("title").asString()).isEqualTo("P0-00.8 发布网关样例问卷");
        // 新版本是一份全新的引擎问卷：答卷不会与被恢复的旧版混在一起。
        assertThat(v3.engineSid()).isNotEqualTo(v1.engineSid()).isNotEqualTo(v2.engineSid());
        assertThat(currentRouteSid()).isEqualTo(v3.engineSid());
        assertThat(surveys.version(ws.owner(), survey, 1).engineSid()).isEqualTo(v1.engineSid());
        assertThat(surveys.version(ws.owner(), survey, 2).engineSid()).isEqualTo(v2.engineSid());
        assertThat(surveys.version(ws.owner(), survey, 2).live()).isFalse();
        // 旧 sid 上的答卷仍认得回同一份问卷。
        assertThat(routes.findByEngine(ws.tenant(), v1.engineInstanceId(), v1.engineSid())).get()
                .extracting(SurveyRoute::publicId).isEqualTo(survey);
    }

    @Test
    void restoringWithAStaleExpectedVersionIsAConflictAndChangesNothing() {
        surveys.saveDraft(ws.owner(), survey, 2, fixture.definitionTitled("第三稿"));

        assertThatThrownBy(() -> surveys.restore(ws.owner(), survey, 1, 2))
                .isInstanceOfSatisfying(SurveyConflictException.class,
                        e -> assertThat(e.code()).isEqualTo("version_conflict"));
        assertThat(draftTitle()).isEqualTo("第三稿");
        assertThat(draftVersion()).isEqualTo(3);
    }

    @Test
    void concurrentRestoresFromTheSameDraftVersionLetExactlyOneWin() throws Exception {
        CountDownLatch start = new CountDownLatch(1);
        List<CompletableFuture<String>> results = new ArrayList<>();
        try (ExecutorService pool = Executors.newFixedThreadPool(WRITERS)) {
            for (int i = 0; i < WRITERS; i++) {
                results.add(CompletableFuture.supplyAsync(() -> restoreConcurrently(start), pool));
            }
            start.countDown();
        }

        List<String> outcomes = results.stream().map(CompletableFuture::join).toList();
        assertThat(outcomes).filteredOn("restored"::equals).hasSize(1);
        assertThat(outcomes).filteredOn("version_conflict"::equals).hasSize(WRITERS - 1);
        assertThat(draftVersion()).isEqualTo(3);
        assertThat(currentRouteSid()).isEqualTo(v2.engineSid());
    }

    private String restoreConcurrently(CountDownLatch start) {
        try {
            start.await();
            surveys.restore(ws.owner(), survey, 1, 2);
            return "restored";
        } catch (SurveyConflictException e) {
            return e.code();
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            return "interrupted";
        }
    }

    @Test
    void restoringAVersionThatWasNeverPublishedIsNotFound() {
        assertThatThrownBy(() -> surveys.restore(ws.owner(), survey, 7, 2))
                .isInstanceOf(SurveyNotFoundException.class);
        assertThat(draftVersion()).isEqualTo(2);
    }

    @Test
    void restoringTheLiveVersionDiscardsUnpublishedDraftEdits() {
        surveys.saveDraft(ws.owner(), survey, 2, fixture.definitionTitled("写坏了"));

        DraftView restored = surveys.restore(ws.owner(), survey, 2, 3);

        assertThat(restored.version()).isEqualTo(4);
        assertThat(restored.definition()).isEqualTo(v2.definition());
        assertThat(currentRouteSid()).isEqualTo(v2.engineSid());
    }

    @Test
    void restoringNeedsEditRightsOnTheSurvey() {
        TenantContext viewer = fixture.member(ws, "stats", "statistics_viewer", survey);

        assertThatThrownBy(() -> surveys.restore(viewer, survey, 1, 2))
                .isInstanceOfSatisfying(SurveyAccessDeniedException.class,
                        e -> assertThat(e.reason()).isEqualTo(DecisionReason.NO_MATCHING_GRANT));
        assertThat(draftVersion()).isEqualTo(2);
    }

    @Test
    void restoringVoidsAnOpenApprovalRequestJustLikeSavingTheDraft() {
        surveys.saveDraft(ws.owner(), survey, 2, fixture.definitionTitled("待审的第三稿"));
        PublishApprovalView submitted = approvals.submit(ws.owner(), survey, 3);

        surveys.restore(ws.owner(), survey, 1, 3);

        assertThat(approvals.get(ws.owner(), submitted.id()).request().status())
                .isEqualTo(PublishApprovalStatus.VOIDED);
    }
}
