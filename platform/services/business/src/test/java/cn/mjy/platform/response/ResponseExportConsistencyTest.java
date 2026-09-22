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
import org.springframework.test.context.TestPropertySource;

/**
 * 导出期间持续有新答卷写入（含比已有答卷号更小的号、另一个版本、另一个代次）：
 * 创建作业时已存在的每一行恰好出现一次；作业创建之后才写入的行一行都不出现；没有任何重复。
 * 批次故意设小，让导出跨越很多批。
 */
@SpringBootTest
@TestPropertySource(properties = "platform.response.export.batch-size=7")
class ResponseExportConsistencyTest {

    private static final int EXISTING = 60;

    @Autowired
    private ExportFixture fixture;

    @Autowired
    private ResponseExportWorker worker;

    private record Key(String version, String sid, String responseId) {
    }

    @Test
    void theExportHoldsExactlyTheRowsThatExistedAtCreationWhileResponsesKeepArriving() throws Exception {
        ResponseFixture responses = fixture.responses();
        Published p = responses.publishedSurvey();
        long sid2 = responses.addVersion(p, List.of("V2A", "V2B", "V2C", "V2D", "V2E"));
        Set<Key> existing = new HashSet<>();
        for (int i = 0; i < EXISTING; i++) {
            long id = 2L * i + 101;
            responses.response(p.tenant(), p.instance(), p.sid(), GENERATION, id, COMPLETED);
            existing.add(new Key("1", Long.toString(p.sid()), Long.toString(id)));
        }
        responses.response(p.tenant(), p.instance(), sid2, GENERATION, 1, SAVED);
        existing.add(new Key("2", Long.toString(sid2), "1"));

        ExportJobView job = fixture.create(p.owner(), p, "csv");

        ExecutorService pool = Executors.newSingleThreadExecutor();
        CountDownLatch started = new CountDownLatch(1);
        Future<?> writer = pool.submit(() -> {
            started.countDown();
            for (int i = 1; i <= EXISTING; i++) {
                responses.response(p.tenant(), p.instance(), p.sid(), GENERATION, 2L * i + 100, SAVED);
                responses.response(p.tenant(), p.instance(), sid2, GENERATION, 1L + i, SAVED);
            }
        });
        started.await(5, TimeUnit.SECONDS);
        // 物化与导出各走几批，中间确定地插入落在已导出区间之前与之后的新答卷。
        for (int round = 0; round < 3; round++) {
            worker.process(p.tenant(), job.jobId(), 2);
            responses.response(p.tenant(), p.instance(), p.sid(), "a-earlier-generation", 5_000 + round, SAVED);
            responses.response(p.tenant(), p.instance(), p.sid(), GENERATION, 1 + round, SAVED);
            responses.response(p.tenant(), p.instance(), p.sid(), GENERATION, 9_000 + round, SAVED);
            fixture.expireLease(p.tenant(), job.jobId());
        }
        ExportJobView done = fixture.runToEnd(p.owner(), job);
        writer.get(60, TimeUnit.SECONDS);
        pool.shutdown();

        List<List<String>> rows = ExportFixture.unzipCsv(fixture.download(p.owner(), done)).get("responses.csv");
        List<Key> seen = new ArrayList<>();
        for (List<String> row : rows.subList(2, rows.size())) {
            seen.add(new Key(row.get(0), row.get(1), row.get(2)));
        }
        assertThat(done.status()).isEqualTo("completed");
        assertThat(new HashSet<>(seen)).as("no duplicates").hasSize(seen.size());
        assertThat(new HashSet<>(seen)).as("exactly the rows that existed at creation").isEqualTo(existing);
        assertThat(done.totalRows()).isEqualTo(existing.size());
        assertThat(done.processedRows()).isEqualTo(existing.size());
    }
}
