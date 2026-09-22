package cn.mjy.platform.survey;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import cn.mjy.platform.access.DecisionReason;
import cn.mjy.platform.shared.TenantContext;
import cn.mjy.platform.survey.SurveyFixture.Workspace;
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
import tools.jackson.databind.node.ObjectNode;

/** 问卷与草稿：稳定的公开 UUID、乐观锁、服务端形状校验；已发布版本不随草稿变化。 */
@SpringBootTest
class SurveyDraftTest {

    private static final int WRITERS = 5;

    @Autowired
    private SurveyFixture fixture;

    @Autowired
    private SurveyService surveys;

    @Autowired
    private SurveyPublishService publisher;

    private Workspace ws;
    private SurveyView created;

    @BeforeEach
    void aSurvey() {
        ws = fixture.workspace();
        created = fixture.newSurvey(ws);
    }

    @Test
    void aNewSurveyIsADraftAtVersionOneWhoseDefinitionCarriesItsPublicUuid() {
        DraftView draft = surveys.draft(ws.owner(), created.id());

        assertThat(created.status()).isEqualTo(SurveyStatus.DRAFT);
        assertThat(created.draftVersion()).isEqualTo(1);
        assertThat(created.title()).isEqualTo("P0-00.8 发布网关样例问卷");
        assertThat(draft.version()).isEqualTo(1);
        assertThat(draft.definition().get("uuid").asString()).isEqualTo(created.id().toString());
    }

    @Test
    void savingWithTheExpectedVersionBumpsTheVersion() {
        DraftView saved = surveys.saveDraft(ws.owner(), created.id(), 1, fixture.definitionTitled("第二稿"));

        assertThat(saved.version()).isEqualTo(2);
        assertThat(surveys.get(ws.owner(), created.id()).title()).isEqualTo("第二稿");
    }

    @Test
    void savingWithAStaleVersionIsAConflict() {
        surveys.saveDraft(ws.owner(), created.id(), 1, fixture.definitionTitled("第二稿"));

        assertThatThrownBy(() -> surveys.saveDraft(ws.owner(), created.id(), 1, fixture.definitionTitled("迟到")))
                .isInstanceOfSatisfying(SurveyConflictException.class,
                        e -> assertThat(e.code()).isEqualTo("version_conflict"));
        assertThat(surveys.get(ws.owner(), created.id()).title()).isEqualTo("第二稿");
    }

    @Test
    void concurrentSavesFromTheSameVersionLetExactlyOneWin() throws Exception {
        CountDownLatch start = new CountDownLatch(1);
        List<CompletableFuture<String>> results = new ArrayList<>();
        try (ExecutorService pool = Executors.newFixedThreadPool(WRITERS)) {
            for (int i = 0; i < WRITERS; i++) {
                String title = "并发-" + i;
                results.add(CompletableFuture.supplyAsync(() -> save(start, title), pool));
            }
            start.countDown();
        }

        List<String> outcomes = results.stream().map(CompletableFuture::join).toList();
        assertThat(outcomes).filteredOn("saved"::equals).hasSize(1);
        assertThat(outcomes).filteredOn("version_conflict"::equals).hasSize(WRITERS - 1);
        assertThat(surveys.draft(ws.owner(), created.id()).version()).isEqualTo(2);
    }

    private String save(CountDownLatch start, String title) {
        try {
            start.await();
            surveys.saveDraft(ws.owner(), created.id(), 1, fixture.definitionTitled(title));
            return "saved";
        } catch (SurveyConflictException e) {
            return e.code();
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            return "interrupted";
        }
    }

    @Test
    void theServerOverridesAClientSuppliedDefinitionUuid() {
        ObjectNode definition = fixture.definition();
        definition.put("uuid", UUID.randomUUID().toString());

        DraftView saved = surveys.saveDraft(ws.owner(), created.id(), 1, definition);

        assertThat(saved.definition().get("uuid").asString()).isEqualTo(created.id().toString());
    }

    @Test
    void aVersionTwoDefinitionWithLogicIsAccepted() {
        // v2 加入了逻辑 DSL（契约 survey-logic-dsl-v1）；逻辑本身由网关在发布时校验，平台只放行版本号。
        ObjectNode definition = fixture.definition();
        definition.put("definitionVersion", 2);

        DraftView saved = surveys.saveDraft(ws.owner(), created.id(), 1, definition);

        assertThat(saved.definition().get("definitionVersion").asInt()).isEqualTo(2);
    }

    @Test
    void aMalformedDefinitionIsRejectedWithEveryProblemListed() {
        ObjectNode definition = fixture.definition();
        definition.put("definitionVersion", 3);
        definition.remove("title");
        definition.putArray("groups");

        assertThatThrownBy(() -> surveys.saveDraft(ws.owner(), created.id(), 1, definition))
                .isInstanceOfSatisfying(InvalidDefinitionException.class, e -> assertThat(e.problems())
                        .anyMatch(p -> p.contains("definitionVersion"))
                        .anyMatch(p -> p.contains("title"))
                        .anyMatch(p -> p.contains("groups")));
        assertThat(surveys.draft(ws.owner(), created.id()).version()).isEqualTo(1);
    }

    @Test
    void questionsNeedAUuidACodeAndATypeAndUuidsMustBeUnique() {
        ObjectNode definition = fixture.definition();
        ObjectNode first = (ObjectNode) definition.get("groups").get(0).get("questions").get(0);
        ObjectNode second = (ObjectNode) definition.get("groups").get(0).get("questions").get(1);
        first.remove("code");
        second.put("uuid", first.get("uuid").asString());
        ((ObjectNode) definition.get("groups").get(1).get("questions").get(0)).put("uuid", "not-a-uuid");

        assertThatThrownBy(() -> surveys.saveDraft(ws.owner(), created.id(), 1, definition))
                .isInstanceOfSatisfying(InvalidDefinitionException.class, e -> assertThat(e.problems())
                        .anyMatch(p -> p.contains("questions[0].code"))
                        .anyMatch(p -> p.contains("duplicate question uuid"))
                        .anyMatch(p -> p.contains("groups[1].questions[0].uuid")));
    }

    @Test
    void aStatisticsViewerCannotEditTheDraft() {
        TenantContext viewer = fixture.member(ws, "stats", "statistics_viewer", created.id());

        assertThatThrownBy(() -> surveys.saveDraft(viewer, created.id(), 1, fixture.definition()))
                .isInstanceOfSatisfying(SurveyAccessDeniedException.class,
                        e -> assertThat(e.reason()).isEqualTo(DecisionReason.NO_MATCHING_GRANT));
        assertThat(surveys.draft(viewer, created.id()).version()).isEqualTo(1);
    }

    @Test
    void creatingASurveyNeedsEditRightsOnTheParent() {
        TenantContext viewer = fixture.member(ws, "stats", "statistics_viewer", ws.project());

        assertThatThrownBy(() -> surveys.create(viewer, ws.project(), fixture.definition()))
                .isInstanceOf(SurveyAccessDeniedException.class);
    }

    @Test
    void thePublishedVersionDoesNotChangeWhenTheDraftIsEditedAfterwards() {
        publisher.publish(ws.owner(), created.id());

        surveys.saveDraft(ws.owner(), created.id(), 1, fixture.definitionTitled("发布后的新草稿"));

        PublishedVersionView published = surveys.version(ws.owner(), created.id(), 1);
        assertThat(published.definition().get("title").asString()).isEqualTo("P0-00.8 发布网关样例问卷");
        assertThat(published.draftVersion()).isEqualTo(1);
        assertThat(surveys.draft(ws.owner(), created.id()).definition().get("title").asString())
                .isEqualTo("发布后的新草稿");
        assertThat(surveys.get(ws.owner(), created.id()).status()).isEqualTo(SurveyStatus.PUBLISHED);
    }

    @Test
    void anUnknownPublishedVersionIsNotFound() {
        assertThatThrownBy(() -> surveys.version(ws.owner(), created.id(), 1))
                .isInstanceOf(SurveyNotFoundException.class);
    }
}
