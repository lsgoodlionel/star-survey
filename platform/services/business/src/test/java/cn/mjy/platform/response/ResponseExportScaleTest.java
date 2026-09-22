package cn.mjy.platform.response;

import static org.assertj.core.api.Assertions.assertThat;

import cn.mjy.platform.response.FakeResponseAnswerSource.Call;
import cn.mjy.platform.response.ResponseFixture.Published;
import java.io.IOException;
import java.io.InputStream;
import java.io.InputStreamReader;
import java.io.LineNumberReader;
import java.nio.charset.StandardCharsets;
import java.util.List;
import java.util.zip.ZipEntry;
import java.util.zip.ZipInputStream;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;

/**
 * 规模：用一条 SQL 生成大量投影行、用合成作答源现算作答，证明导出按批进行（每次读取不超过批大小、
 * 读取次数 = 行数 / 批大小）、无漏无重（答卷号严格递增且首尾吻合），并且堆占用有上界。
 *
 * <p>默认 20 000 行，随套件运行；{@code -Dexport.scale.rows=300000} 跑验收规模（耗时见报告）。
 */
@SpringBootTest
class ResponseExportScaleTest {

    private static final int DEFAULT_ROWS = 20_000;
    private static final long HEAP_HEADROOM_BYTES = 256L * 1024 * 1024;

    @Autowired
    private ExportFixture fixture;

    @Autowired
    private FakeResponseAnswerSource answers;

    @Autowired
    private ResponseExportService exports;

    @Autowired
    private ResponseExportProperties properties;

    @Test
    void aLargeExportRunsInBoundedBatchesWithNoGapsOrDuplicates() throws IOException {
        int rows = Integer.getInteger("export.scale.rows", DEFAULT_ROWS);
        Published p = fixture.responses().publishedSurvey();
        fixture.bulkProjection(p, 1, rows);
        answers.synthesize(p.instance(), (id, field) -> field + "-" + id + "-中文值");

        long started = System.nanoTime();
        ExportJobView job = fixture.create(p.owner(), p, "csv");
        HeapProbe probe = HeapProbe.start();
        ExportJobView done = fixture.runToEnd(p.owner(), job);
        long peakHeap = probe.stop();
        long exportMillis = (System.nanoTime() - started) / 1_000_000;

        long[] check;
        try (InputStream in = exports.download(p.owner(), job.jobId()).content()) {
            check = scanResponses(in);
        }
        answers.recover(p.instance());
        List<Call> calls = answers.callsFor(p.instance());
        int batch = properties.batchSize();
        System.out.printf("EXPORT SCALE rows=%d batch=%d calls=%d millis=%d fileBytes=%d peakHeapMiB=%d%n",
                rows, batch, calls.size(), exportMillis, done.fileSize(), peakHeap / (1024 * 1024));

        assertThat(done.status()).isEqualTo("completed");
        assertThat(done.totalRows()).isEqualTo(rows);
        assertThat(calls).allSatisfy(c -> assertThat(c.responseIds()).hasSizeLessThanOrEqualTo(batch));
        assertThat(calls).hasSize((rows + batch - 1) / batch);
        assertThat(check[0]).as("data rows").isEqualTo(rows);
        assertThat(check[1]).as("first id").isEqualTo(1);
        assertThat(check[2]).as("last id").isEqualTo(rows);
        assertThat(peakHeap).as("heap growth stays bounded").isLessThan(HEAP_HEADROOM_BYTES);
    }

    /** 流式读 responses.csv：返回 {行数, 首个答卷号, 末个答卷号}；答卷号必须严格递增。 */
    private static long[] scanResponses(InputStream zip) throws IOException {
        try (ZipInputStream in = new ZipInputStream(zip)) {
            ZipEntry entry;
            while ((entry = in.getNextEntry()) != null) {
                if (entry.getName().equals("responses.csv")) {
                    return scan(new LineNumberReader(new InputStreamReader(in, StandardCharsets.UTF_8)));
                }
            }
        }
        throw new AssertionError("responses.csv missing");
    }

    private static long[] scan(LineNumberReader reader) throws IOException {
        reader.readLine();
        reader.readLine();
        long count = 0;
        long first = -1;
        long last = -1;
        String line;
        while ((line = reader.readLine()) != null) {
            long id = Long.parseLong(line.split(",", 4)[2]);
            assertThat(id).as("strictly increasing, no duplicates").isGreaterThan(last);
            first = first < 0 ? id : first;
            last = id;
            count++;
        }
        return new long[] {count, first, last};
    }

    /** 粗测处理期间的堆增长峰值（后台线程每 50 ms 采样一次）。 */
    private static final class HeapProbe implements Runnable {

        private final long baseline;
        private volatile boolean running = true;
        private volatile long peak;
        private final Thread thread;

        private HeapProbe() {
            System.gc();
            baseline = used();
            thread = new Thread(this, "heap-probe");
            thread.setDaemon(true);
        }

        static HeapProbe start() {
            HeapProbe probe = new HeapProbe();
            probe.thread.start();
            return probe;
        }

        @Override
        public void run() {
            while (running) {
                peak = Math.max(peak, used() - baseline);
                try {
                    Thread.sleep(50);
                } catch (InterruptedException e) {
                    return;
                }
            }
        }

        long stop() {
            running = false;
            thread.interrupt();
            return peak;
        }

        private static long used() {
            Runtime rt = Runtime.getRuntime();
            return rt.totalMemory() - rt.freeMemory();
        }
    }
}
