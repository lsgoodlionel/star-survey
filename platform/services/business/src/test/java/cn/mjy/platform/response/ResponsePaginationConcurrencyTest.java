package cn.mjy.platform.response;

import static cn.mjy.platform.engine.EngineEventType.COMPLETED;
import static cn.mjy.platform.engine.EngineEventType.SAVED;
import static cn.mjy.platform.response.ResponseFixture.GENERATION;
import static org.assertj.core.api.Assertions.assertThat;

import cn.mjy.platform.response.ResponseFixture.Published;
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
 * 翻页期间持续有新答卷写入（含落在已翻过区间里的答卷号，以及另一个版本的答卷）：
 * 翻页开始时已存在的每一行恰好出现一次，任何一行都不重复。
 */
@SpringBootTest
class ResponsePaginationConcurrencyTest {

    private static final int EXISTING = 60;
    private static final int PAGE = 7;

    @Autowired
    private ResponseFixture fixture;

    @Autowired
    private ResponseQueryService responses;

    private record Key(long sid, long responseId) {
    }

    @Test
    void pagesHaveNoGapsAndNoDuplicatesWhileResponsesArrive() throws Exception {
        Published p = fixture.publishedSurvey();
        long sid2 = fixture.addVersion(p, List.of("V2A", "V2B", "V2C", "V2D", "V2E"));
        Set<Key> existing = new HashSet<>();
        for (int i = 0; i < EXISTING; i++) {
            long id = 2L * i + 1;
            fixture.response(p.tenant(), p.instance(), p.sid(), GENERATION, id, COMPLETED);
            existing.add(new Key(p.sid(), id));
        }
        fixture.response(p.tenant(), p.instance(), sid2, GENERATION, 1, SAVED);
        existing.add(new Key(sid2, 1));

        ExecutorService pool = Executors.newSingleThreadExecutor();
        CountDownLatch started = new CountDownLatch(1);
        Future<?> writer = pool.submit(() -> {
            started.countDown();
            for (int i = 1; i <= EXISTING; i++) {
                fixture.response(p.tenant(), p.instance(), p.sid(), GENERATION, 2L * i, SAVED);
                fixture.response(p.tenant(), p.instance(), sid2, GENERATION, 1L + i, SAVED);
            }
        });
        started.await(5, TimeUnit.SECONDS);

        List<Key> seen = new ArrayList<>();
        String cursor = null;
        long late = 10_000;
        do {
            ResponsePage page = responses.list(p.owner(), p.surveyId(), null, cursor, PAGE);
            page.items().forEach(row -> seen.add(new Key(row.engineSid(), row.responseId())));
            cursor = page.nextCursor();
            // 除了后台写入，每翻一页再确定地插入：一行落在已翻过的区间之前（另一代次，排在前面），一行在最后。
            fixture.response(p.tenant(), p.instance(), p.sid(), "a-earlier-generation", late++, SAVED);
            fixture.response(p.tenant(), p.instance(), p.sid(), GENERATION, late++, SAVED);
        } while (cursor != null);
        writer.get(60, TimeUnit.SECONDS);
        pool.shutdown();

        assertThat(new HashSet<>(seen)).as("no duplicates").hasSize(seen.size());
        assertThat(seen).as("every pre-existing response exactly once").containsAll(existing);
    }
}
