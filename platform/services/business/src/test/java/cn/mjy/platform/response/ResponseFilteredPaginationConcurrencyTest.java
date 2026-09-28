package cn.mjy.platform.response;

import static cn.mjy.platform.engine.EngineEventType.SAVED;
import static cn.mjy.platform.response.ResponseFixture.GENERATION;
import static org.assertj.core.api.Assertions.assertThat;

import cn.mjy.platform.engine.ResponseState;
import cn.mjy.platform.response.ResponseFixture.Published;
import cn.mjy.platform.shared.TenantId;
import java.time.Instant;
import java.util.ArrayList;
import java.util.HashSet;
import java.util.List;
import java.util.Set;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.Future;
import java.util.concurrent.TimeUnit;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;

/**
 * 带复合筛选的翻页期间持续有新答卷写入：无漏重的保证必须与无筛选时同强度
 * （无筛选的那条见 {@link ResponsePaginationConcurrencyTest}）。
 *
 * <p>为什么筛选不削弱这条保证：筛选条件全部下推到 SQL，排序仍是不可变自然键
 * (版本, 代次, 答卷号)，游标仍是上一页最后一行的自然键。本用例用的谓词在翻页期间不会翻转——
 * {@code first_event_at} 写入后不再变，已完成且不会被删的行状态也不再变——
 * 所以「翻页开始时已存在且命中的行恰好出现一次」是确定的。
 *
 * <p>另一半同样重要：不命中的行**一行都不能出现**，无论它是翻页前就在库里的，
 * 还是翻页期间新写入的。
 */
@SpringBootTest
class ResponseFilteredPaginationConcurrencyTest {

    private static final int EXISTING = 40;
    private static final int PAGE = 7;

    private static final Instant WINDOW_FROM = Instant.parse("2026-09-20T00:00:00Z");
    private static final Instant WINDOW_TO = Instant.parse("2026-09-20T23:59:59Z");
    private static final Instant INSIDE = Instant.parse("2026-09-20T12:00:00Z");
    private static final Instant BEFORE = Instant.parse("2026-09-19T12:00:00Z");
    private static final Instant AFTER = Instant.parse("2026-09-21T12:00:00Z");

    /** 不命中的行用这些号段，方便断言"它们一行都没出现"。 */
    private static final long STARTED_TOO_EARLY = 10_000;
    private static final long STARTED_TOO_LATE = 20_000;
    private static final long STILL_IN_PROGRESS = 30_000;
    private static final long WRITTEN_WHILE_PAGING = 40_000;

    @Autowired
    private ResponseFixture fixture;

    @Autowired
    private ResponseQueryService responses;

    private record Key(long sid, long responseId) {
    }

    @Test
    void filteredPagesHaveNoGapsAndNoDuplicatesAndNeverLeakNonMatchingRows() throws Exception {
        Published p = fixture.publishedSurvey();
        long sid2 = fixture.addVersion(p, List.of("V2A", "V2B", "V2C", "V2D", "V2E"));
        Set<Key> matching = new HashSet<>();
        Set<Key> decoys = new HashSet<>();
        seed(p, sid2, matching, decoys);

        ExecutorService pool = Executors.newSingleThreadExecutor();
        CountDownLatch started = new CountDownLatch(1);
        Future<?> writer = pool.submit(() -> arrivals(p, sid2, started));
        started.await(5, TimeUnit.SECONDS);

        List<ResponseRow> seen = pageThrough(p, sid2);
        writer.get(60, TimeUnit.SECONDS);
        pool.shutdown();

        List<Key> keys = seen.stream().map(row -> new Key(row.engineSid(), row.responseId())).toList();
        assertThat(new HashSet<>(keys)).as("no duplicates").hasSize(keys.size());
        assertThat(keys).as("every pre-existing matching response exactly once").containsAll(matching);
        assertThat(keys).as("no pre-existing non-matching response").doesNotContainAnyElementsOf(decoys);
        assertThat(seen).as("every returned row satisfies the composite filter").allSatisfy(row -> {
            assertThat(row.engineSid()).isEqualTo(p.sid());
            assertThat(row.state()).isEqualTo(ResponseState.ENGINE_COMPLETED.dbValue());
            assertThat(row.startedAt()).isBetween(WINDOW_FROM, WINDOW_TO);
        });
    }

    /** 翻页开始前就在库里的行：命中的与四类不命中的。 */
    private void seed(Published p, long sid2, Set<Key> matching, Set<Key> decoys) {
        for (int i = 0; i < EXISTING; i++) {
            long id = 2L * i + 1;
            completed(p.tenant(), p, p.sid(), id, INSIDE);
            matching.add(new Key(p.sid(), id));
        }
        completed(p.tenant(), p, p.sid(), STARTED_TOO_EARLY, BEFORE);
        completed(p.tenant(), p, p.sid(), STARTED_TOO_LATE, AFTER);
        fixture.responseAt(p.tenant(), p.instance(), p.sid(), GENERATION, STILL_IN_PROGRESS, INSIDE, null);
        completed(p.tenant(), p, sid2, 1, INSIDE);
        decoys.add(new Key(p.sid(), STARTED_TOO_EARLY));
        decoys.add(new Key(p.sid(), STARTED_TOO_LATE));
        decoys.add(new Key(p.sid(), STILL_IN_PROGRESS));
        decoys.add(new Key(sid2, 1));
    }

    /** 后台持续写入：一半命中（出现或不出现都合法），一半不命中（绝不能出现）。 */
    private void arrivals(Published p, long sid2, CountDownLatch started) {
        started.countDown();
        for (int i = 1; i <= EXISTING; i++) {
            completed(p.tenant(), p, p.sid(), 2L * i, INSIDE);
            completed(p.tenant(), p, p.sid(), STARTED_TOO_EARLY + i, BEFORE);
            fixture.responseAt(p.tenant(), p.instance(), p.sid(), GENERATION, STILL_IN_PROGRESS + i, INSIDE, null);
            completed(p.tenant(), p, sid2, 1L + i, INSIDE);
        }
    }

    /**
     * 逐页翻完；每翻一页再确定地插入两行不命中的（一行排在已翻过的区间之前的更早代次，一行在最后），
     * 确认它们既不会挤进结果，也不会把游标带偏。
     */
    private List<ResponseRow> pageThrough(Published p, long sid2) {
        ResponseQuery query = ResponseQuery.ALL
                .withState(ResponseState.ENGINE_COMPLETED)
                .withVersion(1)
                .withStarted(WINDOW_FROM, WINDOW_TO)
                .withLimit(PAGE);
        List<ResponseRow> seen = new ArrayList<>();
        String cursor = null;
        long late = WRITTEN_WHILE_PAGING;
        do {
            ResponsePage page = responses.list(p.owner(), p.surveyId(), query.withCursor(cursor));
            seen.addAll(page.items());
            cursor = page.nextCursor();
            fixture.responseAt(p.tenant(), p.instance(), p.sid(), "a-earlier-generation", late++, AFTER, AFTER);
            fixture.response(p.tenant(), p.instance(), p.sid(), GENERATION, late++, SAVED);
            completed(p.tenant(), p, sid2, late++, INSIDE);
        } while (cursor != null);
        return seen;
    }

    private void completed(TenantId tenant, Published p, long sid, long responseId, Instant at) {
        fixture.responseAt(tenant, p.instance(), sid, GENERATION, responseId, at, at);
    }
}
