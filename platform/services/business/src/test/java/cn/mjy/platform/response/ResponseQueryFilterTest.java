package cn.mjy.platform.response;

import static cn.mjy.platform.response.ResponseFixture.GENERATION;
import static cn.mjy.platform.response.ResponseFixture.PLAIN_FIELD;
import static cn.mjy.platform.response.ResponseFixture.SENSITIVE_FIELD;
import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import cn.mjy.platform.access.ResponseFieldPolicy;
import cn.mjy.platform.engine.ResponseState;
import cn.mjy.platform.response.ResponseFixture.Published;
import cn.mjy.platform.shared.TenantContext;
import java.time.Instant;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;

/**
 * 答卷复合筛选（R06-01）：状态、版本、首事件时间区间、完成时间区间可任意组合。
 *
 * <p>筛选条件全部下推到投影表的 SQL 里，所以键集游标的语义与无筛选时完全一样：
 * 页永远是满的（不是"取一页再在内存里扔掉几行"），顺序仍是 (版本, 代次, 答卷号)，字段权限照旧生效。
 */
@SpringBootTest
class ResponseQueryFilterTest {

    /** 固定时间轴，不依赖真实时钟：窗口是 9/20 一整天。 */
    private static final Instant WINDOW_FROM = Instant.parse("2026-09-20T00:00:00Z");
    private static final Instant WINDOW_TO = Instant.parse("2026-09-20T23:59:59Z");
    private static final Instant INSIDE = Instant.parse("2026-09-20T12:00:00Z");
    private static final Instant BEFORE = Instant.parse("2026-09-19T12:00:00Z");
    private static final Instant AFTER = Instant.parse("2026-09-21T12:00:00Z");

    @Autowired
    private ResponseFixture fixture;

    @Autowired
    private ResponseQueryService responses;

    @Autowired
    private FakeResponseAnswerSource answers;

    private Published p;

    @BeforeEach
    void aPublishedSurvey() {
        p = fixture.publishedSurvey();
    }

    // ------------------------------------------------------------------ 辅助

    /** 一份已完成的答卷：首事件与完成事件各用指定时刻。 */
    private void completed(long responseId, Instant startedAt, Instant completedAt) {
        fixture.responseAt(p.tenant(), p.instance(), p.sid(), GENERATION, responseId, startedAt, completedAt);
        answers.put(p.instance(), p.sid(), responseId,
                Map.of(PLAIN_FIELD, "A" + responseId, SENSITIVE_FIELD, "1380000" + responseId));
    }

    /** 一份还在作答的答卷（只有保存事件，completed_at 为空）。 */
    private void inProgress(long responseId, Instant startedAt) {
        fixture.responseAt(p.tenant(), p.instance(), p.sid(), GENERATION, responseId, startedAt, null);
        answers.put(p.instance(), p.sid(), responseId, Map.of(PLAIN_FIELD, "A" + responseId));
    }

    private List<ResponseRow> page(ResponseQuery query, int limit) {
        return responses.list(p.owner(), p.surveyId(), query.withLimit(limit)).items();
    }

    /** 逐页翻完，顺带钉住"带下一页游标的页一定是满的"。 */
    private List<ResponseRow> walk(TenantContext ctx, ResponseQuery query, int limit) {
        List<ResponseRow> rows = new ArrayList<>();
        String cursor = null;
        do {
            ResponsePage current = responses.list(ctx, p.surveyId(), query.withLimit(limit).withCursor(cursor));
            cursor = current.nextCursor();
            if (cursor != null) {
                assertThat(current.items()).as("a page with a next cursor is full").hasSize(limit);
            }
            rows.addAll(current.items());
        } while (cursor != null);
        return rows;
    }

    private static List<Long> ids(List<ResponseRow> rows) {
        return rows.stream().map(ResponseRow::responseId).toList();
    }

    // ------------------------------------------------------------------ 单个条件

    @Test
    void theStartedRangeKeepsOnlyResponsesFirstSeenInTheWindow() {
        completed(1, BEFORE, INSIDE);
        completed(2, INSIDE, INSIDE);
        completed(3, INSIDE, AFTER);
        completed(4, AFTER, AFTER);

        ResponseQuery query = ResponseQuery.ALL.withStarted(WINDOW_FROM, WINDOW_TO);

        assertThat(ids(page(query, 10))).containsExactly(2L, 3L);
    }

    @Test
    void theStartedRangeBoundsAreInclusive() {
        completed(1, WINDOW_FROM, INSIDE);
        completed(2, WINDOW_TO, INSIDE);
        completed(3, WINDOW_FROM.minusMillis(1), INSIDE);
        completed(4, WINDOW_TO.plusMillis(1), INSIDE);

        assertThat(ids(page(ResponseQuery.ALL.withStarted(WINDOW_FROM, WINDOW_TO), 10))).containsExactly(1L, 2L);
    }

    @Test
    void anOpenEndedStartedRangeFiltersOnOneSideOnly() {
        completed(1, BEFORE, INSIDE);
        completed(2, INSIDE, INSIDE);
        completed(3, AFTER, AFTER);

        assertThat(ids(page(ResponseQuery.ALL.withStarted(WINDOW_FROM, null), 10))).containsExactly(2L, 3L);
        assertThat(ids(page(ResponseQuery.ALL.withStarted(null, WINDOW_TO), 10))).containsExactly(1L, 2L);
    }

    @Test
    void theCompletedRangeKeepsOnlyResponsesCompletedInTheWindowAndDropsUnfinishedOnes() {
        completed(1, BEFORE, BEFORE);
        completed(2, BEFORE, INSIDE);
        completed(3, INSIDE, AFTER);
        inProgress(4, INSIDE);

        ResponseQuery query = ResponseQuery.ALL.withCompleted(WINDOW_FROM, WINDOW_TO);

        assertThat(ids(page(query, 10))).as("未完成的答卷没有完成时刻，不在任何完成区间里").containsExactly(2L);
    }

    @Test
    void theVersionKeepsOnlyThatVersionsResponses() {
        completed(1, INSIDE, INSIDE);
        long sid2 = fixture.addVersion(p, List.of("V2A", "V2B", "V2C", "V2D", "V2E"));
        fixture.responseAt(p.tenant(), p.instance(), sid2, GENERATION, 7, INSIDE, INSIDE);

        List<ResponseRow> v1 = page(ResponseQuery.ALL.withVersion(1), 10);
        List<ResponseRow> v2 = page(ResponseQuery.ALL.withVersion(2), 10);

        assertThat(v1).singleElement().satisfies(row -> {
            assertThat(row.responseId()).isEqualTo(1L);
            assertThat(row.engineSid()).isEqualTo(p.sid());
        });
        assertThat(v2).singleElement().satisfies(row -> {
            assertThat(row.responseId()).isEqualTo(7L);
            assertThat(row.engineSid()).isEqualTo(sid2);
        });
        assertThat(ids(page(ResponseQuery.ALL, 10))).containsExactly(1L, 7L);
    }

    @Test
    void anUnknownVersionYieldsAnEmptyPageRatherThanAnError() {
        completed(1, INSIDE, INSIDE);

        ResponsePage result = responses.list(p.owner(), p.surveyId(), ResponseQuery.ALL.withVersion(99));

        assertThat(result.items()).isEmpty();
        assertThat(result.nextCursor()).isNull();
    }

    // ------------------------------------------------------------------ 复合

    @Test
    void stateVersionAndBothTimeRangesCombine() {
        completed(1, INSIDE, INSIDE);            // 命中
        completed(2, INSIDE, INSIDE);            // 命中
        completed(3, BEFORE, INSIDE);            // 首事件太早
        completed(4, INSIDE, AFTER);             // 完成太晚
        inProgress(5, INSIDE);                   // 状态不符
        long sid2 = fixture.addVersion(p, List.of("V2A", "V2B", "V2C", "V2D", "V2E"));
        fixture.responseAt(p.tenant(), p.instance(), sid2, GENERATION, 1, INSIDE, INSIDE); // 版本不符

        ResponseQuery query = ResponseQuery.ALL
                .withState(ResponseState.ENGINE_COMPLETED)
                .withVersion(1)
                .withStarted(WINDOW_FROM, WINDOW_TO)
                .withCompleted(WINDOW_FROM, WINDOW_TO);

        List<ResponseRow> rows = walk(p.owner(), query, 1);

        assertThat(ids(rows)).containsExactly(1L, 2L);
        assertThat(rows).allSatisfy(row -> assertThat(row.state()).isEqualTo("engine_completed"));
    }

    /**
     * 筛选下推的钉子：命中的行与不命中的行交替排列时，带下一页游标的页必须都是满的。
     * 若筛选是"先取一页再在内存里扔掉不命中的行"，这里会出现半页甚至空页。
     */
    @Test
    void filteredPagesAreFullBecauseTheFilterRunsInSql() {
        for (long id = 1; id <= 12; id++) {
            completed(id, id % 2 == 1 ? INSIDE : AFTER, INSIDE);
        }

        ResponseQuery query = ResponseQuery.ALL.withStarted(WINDOW_FROM, WINDOW_TO);
        ResponsePage first = responses.list(p.owner(), p.surveyId(), query.withLimit(2));

        assertThat(ids(first.items())).containsExactly(1L, 3L);
        assertThat(first.nextCursor()).isNotNull();
        assertThat(ids(walk(p.owner(), query, 2))).containsExactly(1L, 3L, 5L, 7L, 9L, 11L);
    }

    @Test
    void filteredPagingVisitsTheSameRowsAsUnfilteredPagingRestrictedToTheWindow() {
        for (long id = 1; id <= 9; id++) {
            completed(id, id <= 5 ? INSIDE : AFTER, INSIDE);
        }

        List<Long> filtered = ids(walk(p.owner(), ResponseQuery.ALL.withStarted(WINDOW_FROM, WINDOW_TO), 2));
        List<Long> byHand = walk(p.owner(), ResponseQuery.ALL, 2).stream()
                .filter(row -> !row.startedAt().isBefore(WINDOW_FROM) && !row.startedAt().isAfter(WINDOW_TO))
                .map(ResponseRow::responseId)
                .toList();

        assertThat(filtered).isEqualTo(byHand).containsExactly(1L, 2L, 3L, 4L, 5L);
    }

    // ------------------------------------------------------------------ 权限与校验

    @Test
    void sensitiveColumnsStayMaskedUnderFilters() {
        completed(1, INSIDE, INSIDE);
        TenantContext manager = fixture.member(p, "pm", "project_manager");

        ResponsePage result = responses.list(manager, p.surveyId(), ResponseQuery.ALL
                .withState(ResponseState.ENGINE_COMPLETED)
                .withStarted(WINDOW_FROM, WINDOW_TO));

        assertThat(result.sensitiveRevealed()).isFalse();
        assertThat(result.items()).singleElement().satisfies(row -> {
            assertThat(row.answers().get(SENSITIVE_FIELD)).isEqualTo(ResponseFieldPolicy.MASK);
            assertThat(row.answers().get(PLAIN_FIELD)).isEqualTo("A1");
        });
    }

    @Test
    void aStatisticsViewerCannotUseFiltersToReachDetailRows() {
        completed(1, INSIDE, INSIDE);
        TenantContext stats = fixture.member(p, "stats", "statistics_viewer");

        assertThatThrownBy(() -> responses.list(stats, p.surveyId(),
                ResponseQuery.ALL.withStarted(WINDOW_FROM, WINDOW_TO)))
                .isInstanceOf(ResponseAccessDeniedException.class);
    }

    @Test
    void anInvertedRangeOrANonPositiveVersionIsRejected() {
        assertThatThrownBy(() -> ResponseQuery.ALL.withStarted(WINDOW_TO, WINDOW_FROM))
                .isInstanceOf(InvalidResponseQueryException.class)
                .hasMessageContaining("startedFrom");
        assertThatThrownBy(() -> ResponseQuery.ALL.withCompleted(WINDOW_TO, WINDOW_FROM))
                .isInstanceOf(InvalidResponseQueryException.class)
                .hasMessageContaining("completedFrom");
        assertThatThrownBy(() -> ResponseQuery.ALL.withVersion(0))
                .isInstanceOf(InvalidResponseQueryException.class)
                .hasMessageContaining("version");
    }

    @Test
    void theCursorAndLimitAreValidatedByTheQueryObject() {
        assertThatThrownBy(() -> ResponseQuery.ALL.withCursor("not-a-cursor"))
                .isInstanceOf(InvalidResponseQueryException.class);
        assertThatThrownBy(() -> ResponseQuery.ALL.withLimit(0))
                .isInstanceOf(InvalidResponseQueryException.class);
        assertThatThrownBy(() -> ResponseQuery.ALL.withLimit(ResponseQuery.MAX_LIMIT + 1))
                .isInstanceOf(InvalidResponseQueryException.class);
        assertThat(ResponseQuery.ALL.limit()).isEqualTo(ResponseQuery.DEFAULT_LIMIT);
        assertThat(ResponseQuery.ALL.withCursor(null).after()).isNull();
    }

    @Test
    void queryParametersAreParsedFromTextAndBadTextIsRejected() {
        ResponseQuery parsed = ResponseQuery.parse("engine_completed", 2,
                "2026-09-20T00:00:00Z", "2026-09-20T23:59:59Z", null, "2026-09-21T00:00:00Z", null, 7);

        assertThat(parsed.state()).isEqualTo(ResponseState.ENGINE_COMPLETED);
        assertThat(parsed.version()).isEqualTo(2);
        assertThat(parsed.startedFrom()).isEqualTo(WINDOW_FROM);
        assertThat(parsed.startedTo()).isEqualTo(WINDOW_TO);
        assertThat(parsed.completedFrom()).isNull();
        assertThat(parsed.completedTo()).isEqualTo(Instant.parse("2026-09-21T00:00:00Z"));
        assertThat(parsed.limit()).isEqualTo(7);
        assertThat(ResponseQuery.parse(null, null, null, null, null, null, null, null))
                .isEqualTo(ResponseQuery.ALL);

        assertThatThrownBy(() -> ResponseQuery.parse(null, null, "2026-09-20", null, null, null, null, null))
                .isInstanceOf(InvalidResponseQueryException.class)
                .hasMessageContaining("startedFrom");
        assertThatThrownBy(() -> ResponseQuery.parse("finished", null, null, null, null, null, null, null))
                .isInstanceOf(InvalidResponseQueryException.class);
    }

    @Test
    void theStateOnlyEntryPointStillBehavesLikeBefore() {
        completed(1, INSIDE, INSIDE);
        inProgress(2, INSIDE);

        assertThat(ids(responses.list(p.owner(), p.surveyId(), "engine_completed", null, 10).items()))
                .containsExactly(1L);
        assertThat(ids(responses.list(p.owner(), p.surveyId(), null, null, 10).items()))
                .containsExactly(1L, 2L);
    }

    @Test
    void aCursorTakenWithAFilterCarriesOnlyTheKeysetPosition() {
        for (long id = 1; id <= 4; id++) {
            completed(id, INSIDE, INSIDE);
        }
        ResponseQuery query = ResponseQuery.ALL.withStarted(WINDOW_FROM, WINDOW_TO).withLimit(2);

        String cursor = responses.list(p.owner(), p.surveyId(), query).nextCursor();
        ResponseCursor decoded = ResponseCursor.decode(cursor);

        assertThat(decoded.version()).isEqualTo(1);
        assertThat(decoded.responseId()).isEqualTo(2L);
        assertThat(ids(responses.list(p.owner(), p.surveyId(), query.withCursor(cursor)).items()))
                .containsExactly(3L, 4L);
    }
}
