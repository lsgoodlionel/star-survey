package cn.mjy.platform.response;

import static cn.mjy.platform.engine.EngineEventType.COMPLETED;
import static cn.mjy.platform.engine.EngineEventType.DELETED;
import static cn.mjy.platform.engine.EngineEventType.SAVED;
import static cn.mjy.platform.response.ResponseFixture.GENERATION;
import static cn.mjy.platform.response.ResponseFixture.PLAIN_FIELD;
import static cn.mjy.platform.response.ResponseFixture.SENSITIVE_FIELD;
import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import cn.mjy.platform.access.DecisionReason;
import cn.mjy.platform.access.ResponseFieldPolicy;
import cn.mjy.platform.response.ResponseFixture.Published;
import cn.mjy.platform.shared.TenantContext;
import cn.mjy.platform.shared.tenant.TenantScope;
import java.time.Instant;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.jdbc.core.simple.JdbcClient;

/** 答卷明细查询：分页、版本标签、状态筛选、字段权限、代次与删除，以及取不到作答时失败即关闭。 */
@SpringBootTest
class ResponseQueryTest {

    @Autowired
    private ResponseFixture fixture;

    @Autowired
    private ResponseQueryService responses;

    @Autowired
    private FakeResponseAnswerSource answers;

    @Autowired
    private TenantScope tenantScope;

    @Autowired
    private JdbcClient jdbc;

    private Published p;

    @BeforeEach
    void aPublishedSurvey() {
        p = fixture.publishedSurvey();
    }

    private void completed(long responseId, String plain, String sensitive) {
        fixture.response(p.tenant(), p.instance(), p.sid(), GENERATION, responseId, COMPLETED);
        answers.put(p.instance(), p.sid(), responseId, Map.of(PLAIN_FIELD, plain, SENSITIVE_FIELD, sensitive));
    }

    private List<ResponseRow> all(TenantContext ctx, String state, int limit) {
        List<ResponseRow> rows = new ArrayList<>();
        String cursor = null;
        do {
            ResponsePage page = responses.list(ctx, p.surveyId(), state, cursor, limit);
            assertThat(page.items()).hasSizeLessThanOrEqualTo(limit);
            rows.addAll(page.items());
            cursor = page.nextCursor();
        } while (cursor != null);
        return rows;
    }

    @Test
    void theOwnerSeesAnswersInStableOrderAcrossPages() {
        for (long id = 1; id <= 7; id++) {
            completed(id, "A" + id, "1380000000" + id);
        }

        List<ResponseRow> rows = all(p.owner(), null, 3);

        assertThat(rows).extracting(ResponseRow::responseId).containsExactly(1L, 2L, 3L, 4L, 5L, 6L, 7L);
        assertThat(rows).allSatisfy(row -> {
            assertThat(row.version()).isEqualTo(1);
            assertThat(row.state()).isEqualTo("engine_completed");
            assertThat(row.answersStatus()).isEqualTo(AnswersStatus.AVAILABLE);
            assertThat(row.completedAt()).isNotNull();
        });
        assertThat(rows.get(0).answers()).containsEntry(PLAIN_FIELD, "A1").containsEntry(SENSITIVE_FIELD, "13800000001");
    }

    @Test
    void theLastPageHasNoNextCursor() {
        completed(1, "A", "B");
        completed(2, "A", "B");

        ResponsePage page = responses.list(p.owner(), p.surveyId(), null, null, 2);

        assertThat(page.items()).hasSize(2);
        assertThat(page.nextCursor()).isNull();
    }

    @Test
    void aRawViewerWithoutTheSensitivePermissionSeesMaskedSensitiveFields() {
        completed(1, "非常满意", "13812345678");
        TenantContext manager = fixture.member(p, "pm", "project_manager");

        ResponsePage page = responses.list(manager, p.surveyId(), null, null, 10);

        assertThat(page.sensitiveRevealed()).isFalse();
        assertThat(page.items().get(0).answers())
                .containsEntry(PLAIN_FIELD, "非常满意")
                .containsEntry(SENSITIVE_FIELD, ResponseFieldPolicy.MASK);
    }

    @Test
    void aRawViewerWithTheSensitivePermissionSeesClearValues() {
        completed(1, "非常满意", "13812345678");
        TenantContext raw = fixture.member(p, "raw", "raw_data_viewer");

        ResponsePage page = responses.list(raw, p.surveyId(), null, null, 10);

        assertThat(page.sensitiveRevealed()).isTrue();
        assertThat(page.items().get(0).answers()).containsEntry(SENSITIVE_FIELD, "13812345678");
    }

    @Test
    void aStatisticsViewerCannotListRowsAndTheGatewayIsNeverAsked() {
        completed(1, "A", "13812345678");
        TenantContext stats = fixture.member(p, "stats", "statistics_viewer");

        assertThatThrownBy(() -> responses.list(stats, p.surveyId(), null, null, 10))
                .isInstanceOfSatisfying(ResponseAccessDeniedException.class,
                        e -> assertThat(e.reason()).isEqualTo(DecisionReason.NO_MATCHING_GRANT));
        assertThat(answers.callsFor(p.instance())).isEmpty();
    }

    @Test
    void aStatisticsViewerGetsCountsByVersionAndState() {
        completed(1, "A", "B");
        fixture.response(p.tenant(), p.instance(), p.sid(), GENERATION, 2, SAVED);
        fixture.response(p.tenant(), p.instance(), p.sid(), GENERATION, 3, DELETED);
        TenantContext stats = fixture.member(p, "stats", "statistics_viewer");

        ResponseSummary summary = responses.summary(stats, p.surveyId());

        assertThat(summary.versions()).singleElement().satisfies(v -> {
            assertThat(v.version()).isEqualTo(1);
            assertThat(v.counts()).isEqualTo(new ResponseSummary.Counts(1, 1, 1));
        });
        assertThat(summary.total()).isEqualTo(new ResponseSummary.Counts(1, 1, 1));
    }

    @Test
    void aMemberWithNeitherPermissionCannotReadTheSummary() {
        TenantContext interviewer = fixture.member(p, "iv", "interviewer");

        assertThatThrownBy(() -> responses.summary(interviewer, p.surveyId()))
                .isInstanceOf(ResponseAccessDeniedException.class);
    }

    @Test
    void theStateFilterOnlyReturnsMatchingRows() {
        completed(1, "A", "B");
        fixture.response(p.tenant(), p.instance(), p.sid(), GENERATION, 2, SAVED);
        completed(3, "C", "D");

        assertThat(all(p.owner(), "engine_completed", 1)).extracting(ResponseRow::responseId).containsExactly(1L, 3L);
        assertThat(all(p.owner(), "in_progress", 1)).extracting(ResponseRow::responseId).containsExactly(2L);
    }

    @Test
    void anUnknownStateOrMalformedCursorOrLimitIsRejected() {
        assertThatThrownBy(() -> responses.list(p.owner(), p.surveyId(), "finished", null, 10))
                .isInstanceOf(InvalidResponseQueryException.class);
        assertThatThrownBy(() -> responses.list(p.owner(), p.surveyId(), null, "not-a-cursor", 10))
                .isInstanceOf(InvalidResponseQueryException.class);
        assertThatThrownBy(() -> responses.list(p.owner(), p.surveyId(), null, null, 0))
                .isInstanceOf(InvalidResponseQueryException.class);
        assertThatThrownBy(() -> responses.list(p.owner(), p.surveyId(), null, null, ResponseQueryService.MAX_LIMIT + 1))
                .isInstanceOf(InvalidResponseQueryException.class);
    }

    @Test
    void responsesOfTwoVersionsAreTaggedAndNeverMixed() {
        long sid2 = fixture.addVersion(p, List.of("V2A", "V2B", "V2C", "V2D", "V2E"));
        completed(1, "v1-answer", "v1-secret");
        fixture.response(p.tenant(), p.instance(), sid2, GENERATION, 1, COMPLETED);
        answers.put(p.instance(), sid2, 1, Map.of("V2A", "v2-answer", "V2B", "v2-secret"));

        List<ResponseRow> rows = all(p.owner(), null, 1);

        assertThat(rows).hasSize(2);
        assertThat(rows.get(0).version()).isEqualTo(1);
        assertThat(rows.get(0).engineSid()).isEqualTo(p.sid());
        assertThat(rows.get(0).answers()).containsEntry(PLAIN_FIELD, "v1-answer").doesNotContainKey("V2A");
        assertThat(rows.get(1).version()).isEqualTo(2);
        assertThat(rows.get(1).engineSid()).isEqualTo(sid2);
        assertThat(rows.get(1).answers()).containsEntry("V2A", "v2-answer").doesNotContainKey(PLAIN_FIELD);
        assertThat(answers.callsFor(p.instance()))
                .anySatisfy(c -> assertThat(c.engineSid()).isEqualTo(p.sid()))
                .anySatisfy(c -> assertThat(c.engineSid()).isEqualTo(sid2))
                .allSatisfy(c -> assertThat(c.responseIds()).containsExactly(1L));
    }

    @Test
    void theSecondVersionsSensitiveColumnIsMaskedToo() {
        long sid2 = fixture.addVersion(p, List.of("V2A", "V2B", "V2C", "V2D", "V2E"));
        fixture.response(p.tenant(), p.instance(), sid2, GENERATION, 1, COMPLETED);
        answers.put(p.instance(), sid2, 1, Map.of("V2A", "plain", "V2B", "13812345678"));
        TenantContext manager = fixture.member(p, "pm", "project_manager");

        ResponseRow row = responses.list(manager, p.surveyId(), null, null, 10).items().get(0);

        assertThat(row.answers()).containsEntry("V2A", "plain").containsEntry("V2B", ResponseFieldPolicy.MASK);
    }

    @Test
    void deletedResponsesAreTombstonesWithoutAnswers() {
        fixture.response(p.tenant(), p.instance(), p.sid(), GENERATION, 1, DELETED);

        ResponseRow row = responses.list(p.owner(), p.surveyId(), null, null, 10).items().get(0);

        assertThat(row.state()).isEqualTo("deleted");
        assertThat(row.deletedAt()).isNotNull();
        assertThat(row.answersStatus()).isEqualTo(AnswersStatus.DELETED);
        assertThat(row.answers()).isNull();
        assertThat(answers.callsFor(p.instance())).isEmpty();
    }

    @Test
    void aResponseTheEngineNoLongerHasIsReportedMissing() {
        fixture.response(p.tenant(), p.instance(), p.sid(), GENERATION, 1, COMPLETED);

        ResponseRow row = responses.list(p.owner(), p.surveyId(), null, null, 10).items().get(0);

        assertThat(row.answersStatus()).isEqualTo(AnswersStatus.MISSING);
        assertThat(row.answers()).isNull();
    }

    @Test
    void rowsOfAnOlderGenerationAreArchivedAndNeverGetTheNewTablesAnswers() {
        Instant earlier = Instant.parse("2026-09-01T00:00:00Z");
        fixture.event(p.tenant(), p.instance(), p.sid(), "gen-old", 1, SAVED, earlier);
        fixture.event(p.tenant(), p.instance(), p.sid(), "gen-new", 1, SAVED, earlier.plusSeconds(3600));
        answers.put(p.instance(), p.sid(), 1, Map.of(PLAIN_FIELD, "belongs-to-gen-new"));

        List<ResponseRow> rows = all(p.owner(), null, 10);

        assertThat(rows).hasSize(2);
        ResponseRow old = rows.stream().filter(r -> r.generation().equals("gen-old")).findFirst().orElseThrow();
        ResponseRow current = rows.stream().filter(r -> r.generation().equals("gen-new")).findFirst().orElseThrow();
        assertThat(old.answersStatus()).isEqualTo(AnswersStatus.ARCHIVED);
        assertThat(old.answers()).isNull();
        assertThat(current.answers()).containsEntry(PLAIN_FIELD, "belongs-to-gen-new");
    }

    @Test
    void whenTheGatewayFailsTheQueryFailsInsteadOfReturningRowsWithoutAnswers() {
        completed(1, "A", "B");
        answers.failFor(p.instance());

        assertThatThrownBy(() -> responses.list(p.owner(), p.surveyId(), null, null, 10))
                .isInstanceOf(ResponseAnswersUnavailableException.class);
    }

    @Test
    void aSurveyWithoutResponsesHasAnEmptyLastPage() {
        ResponsePage page = responses.list(p.owner(), p.surveyId(), null, null, 10);

        assertThat(page.items()).isEmpty();
        assertThat(page.nextCursor()).isNull();
    }

    @Test
    void everyRawListingIsAudited() {
        completed(1, "A", "B");
        TenantContext manager = fixture.member(p, "pm", "project_manager");

        responses.list(manager, p.surveyId(), null, null, 10);

        long audits = tenantScope.call(p.tenant(), () -> jdbc
                .sql("SELECT COUNT(*) FROM audit_log WHERE action = 'response.list' AND actor_id = 'pm' AND resource = :r")
                .param("r", "survey/" + p.surveyId() + "/responses")
                .query(Long.class).single());
        assertThat(audits).isEqualTo(1);
    }
}
