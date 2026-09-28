package cn.mjy.platform.shared.storage;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.time.Duration;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;

/**
 * S3 兼容对象存储实现：与本地实现**同一份**行为契约（父类），外加这一侧特有的线上行为——
 * 签名、键前缀、按前缀删要翻页、HTTP 故障不被当成"对象不存在"。
 *
 * <p>对面是 {@link FakeS3}：一份最小的 S3，SigV4 校验按规格**独立**写了一遍。
 */
class S3BlobStoreTest extends BlobStoreContractTest {

    private static final String PREFIX = "exports";

    private FakeS3 s3;

    @BeforeEach
    void startFakeS3() throws IOException {
        s3 = new FakeS3();
    }

    @AfterEach
    void stopFakeS3() {
        s3.close();
    }

    private BlobStoreProperties properties() {
        return new BlobStoreProperties(BlobStoreProperties.S3, s3.endpoint(), FakeS3.REGION, FakeS3.BUCKET,
                FakeS3.ACCESS_KEY, FakeS3.SECRET_KEY, true, Duration.ofSeconds(10));
    }

    @Override
    protected BlobStore store() {
        return new S3BlobStore(properties(), PREFIX);
    }

    private static void write(BlobStore store, String key, String content) throws IOException {
        try (BlobStore.Upload upload = store.create(key)) {
            upload.stream().write(content.getBytes(StandardCharsets.UTF_8));
            upload.commit();
        }
    }

    /** 每一次请求都签过名：假 S3 一次都没有因为签名而拒绝。 */
    @Test
    void everyRequestIsSigned() throws IOException {
        BlobStore store = store();

        write(store, "t1/job-1/a", "x");
        store.exists("t1/job-1/a");
        store.size("t1/job-1/a");
        store.open("t1/job-1/a").close();
        store.deletePrefix("t1/job-1");

        assertThat(s3.refusals()).isZero();
        assertThat(s3.requests()).isNotEmpty();
    }

    /** 密钥不对时**不会**被当成"对象不存在"：403 是故障，不是缺失。 */
    @Test
    void aRefusedRequestIsAFailureNotAnAbsence() {
        BlobStoreProperties wrong = new BlobStoreProperties(BlobStoreProperties.S3, s3.endpoint(), FakeS3.REGION,
                FakeS3.BUCKET, FakeS3.ACCESS_KEY, "not-the-secret", true, Duration.ofSeconds(10));
        BlobStore store = new S3BlobStore(wrong, PREFIX);

        assertThatThrownBy(() -> store.open("t1/a")).isInstanceOf(IOException.class);
        assertThatThrownBy(() -> store.size("t1/a")).isInstanceOf(IOException.class);
        assertThatThrownBy(() -> store.exists("t1/a")).isInstanceOf(RuntimeException.class);
        assertThat(s3.refusals()).isPositive();
    }

    /** 两个模块在同一个桶里各占一个前缀：导出写的对象不会落到资产的地盘上。 */
    @Test
    void eachModuleKeepsToItsOwnKeyPrefix() throws IOException {
        BlobStore exports = new S3BlobStore(properties(), "exports");
        BlobStore assets = new S3BlobStore(properties(), "assets");

        write(exports, "t1/job-1/a", "导出");
        write(assets, "t1/job-1/a", "资产");

        assertThat(s3.objects()).containsOnlyKeys("exports/t1/job-1/a", "assets/t1/job-1/a");
        assertThat(new String(s3.objects().get("exports/t1/job-1/a"), StandardCharsets.UTF_8)).isEqualTo("导出");
        assertThat(new String(s3.objects().get("assets/t1/job-1/a"), StandardCharsets.UTF_8)).isEqualTo("资产");
    }

    /** 一个前缀下的对象多到一页列不完时要续读：漏掉一页等于到期清理留下孤儿对象。 */
    @Test
    void deletingAPrefixPagesThroughTheListing() throws IOException {
        BlobStore store = store();
        for (int i = 0; i < 7; i++) {
            write(store, "t1/job-1/part-" + i, "x");
        }
        write(store, "t1/job-2/part-0", "x");

        store.deletePrefix("t1/job-1");

        assertThat(s3.objects()).containsOnlyKeys("exports/t1/job-2/part-0");
        assertThat(s3.requests().stream().filter(line -> line.contains("list-type=2")).count())
                .as("7 个对象、每页 2 个：必须翻页").isGreaterThan(1);
    }

    /** 前缀删只按"整段"匹配：job-1 不能顺手把 job-10 也删了。 */
    @Test
    void deletingAPrefixDoesNotTakeSiblingsWhoseNameStartsTheSame() throws IOException {
        BlobStore store = store();
        write(store, "t1/job-1/a", "x");
        write(store, "t1/job-10/a", "x");

        store.deletePrefix("t1/job-1");

        assertThat(s3.objects()).containsOnlyKeys("exports/t1/job-10/a");
    }

    /** 提交之前一个字节都没发出去：没提交就关闭时，对面连一次 PUT 都没收到。 */
    @Test
    void anAbandonedUploadNeverReachesTheObjectStore() throws IOException {
        BlobStore store = store();

        try (BlobStore.Upload upload = store.create("t1/job-1/a")) {
            upload.stream().write("半截".getBytes(StandardCharsets.UTF_8));
        }

        assertThat(s3.objects()).isEmpty();
        assertThat(s3.requests()).noneMatch(line -> line.startsWith("PUT"));
    }
}
