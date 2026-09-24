package cn.mjy.platform.shared.storage;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import java.io.IOException;
import java.io.InputStream;
import java.nio.charset.StandardCharsets;
import org.junit.jupiter.api.Test;

/**
 * {@link BlobStore} 的**行为契约**。导出与资产共用同一份抽象（ADR 0015 决定 2、ADR 0019 决定 2），
 * 所以也必须共用同一份契约测试：每个实现继承本类，一条都不能少。
 *
 * <p>这是"两处共用一套存储"真正的落点。没有它，本地实现与对象存储实现的差异
 * （提交前可见、覆盖只改了一半、按前缀删漏了一层）只会在切到生产之后才暴露，
 * 而且只在两个模块中的一个上暴露。
 */
abstract class BlobStoreContractTest {

    private static final String KEY = "t1/job-1/export.zip";
    private static final String CONTENT = "一些字节 bytes";

    /** 每个测试一个干净的存储。 */
    protected abstract BlobStore store() throws IOException;

    private static void write(BlobStore store, String key, String content) throws IOException {
        try (BlobStore.Upload upload = store.create(key)) {
            upload.stream().write(content.getBytes(StandardCharsets.UTF_8));
            upload.commit();
        }
    }

    private static String read(BlobStore store, String key) throws IOException {
        try (InputStream in = store.open(key)) {
            return new String(in.readAllBytes(), StandardCharsets.UTF_8);
        }
    }

    @Test
    void writesAndReadsBackTheSameBytes() throws IOException {
        BlobStore store = store();

        write(store, KEY, CONTENT);

        assertThat(read(store, KEY)).isEqualTo(CONTENT);
        assertThat(store.exists(KEY)).isTrue();
        assertThat(store.size(KEY)).isEqualTo(CONTENT.getBytes(StandardCharsets.UTF_8).length);
    }

    /** 提交之前对象不存在：读者永远看不见半个文件。 */
    @Test
    void anUncommittedUploadIsInvisible() throws IOException {
        BlobStore store = store();

        try (BlobStore.Upload upload = store.create(KEY)) {
            upload.stream().write(CONTENT.getBytes(StandardCharsets.UTF_8));
            assertThat(store.exists(KEY)).isFalse();
            upload.commit();
        }

        assertThat(store.exists(KEY)).isTrue();
    }

    /** 没提交就关闭（写出错、执行者死掉）等于放弃；旧对象保持不变。 */
    @Test
    void abandoningAnUploadLeavesTheOldObjectUntouched() throws IOException {
        BlobStore store = store();
        write(store, KEY, CONTENT);

        try (BlobStore.Upload upload = store.create(KEY)) {
            upload.stream().write("半截".getBytes(StandardCharsets.UTF_8));
        }

        assertThat(read(store, KEY)).isEqualTo(CONTENT);
    }

    /** 重写是**整体替换**，不是追加也不是就地改：崩溃恢复重做一批要覆盖同名分片。 */
    @Test
    void committingOverAnExistingObjectReplacesItWhole() throws IOException {
        BlobStore store = store();
        write(store, KEY, "很长很长的旧内容 old and long");

        write(store, KEY, "短");

        assertThat(read(store, KEY)).isEqualTo("短");
        assertThat(store.size(KEY)).isEqualTo("短".getBytes(StandardCharsets.UTF_8).length);
    }

    @Test
    void anEmptyObjectIsStillAnObject() throws IOException {
        BlobStore store = store();

        write(store, KEY, "");

        assertThat(store.exists(KEY)).isTrue();
        assertThat(store.size(KEY)).isZero();
        assertThat(read(store, KEY)).isEmpty();
    }

    @Test
    void readingSomethingThatIsNotThereFails() throws IOException {
        BlobStore store = store();

        assertThat(store.exists(KEY)).isFalse();
        assertThatThrownBy(() -> store.open(KEY)).isInstanceOf(IOException.class);
        assertThatThrownBy(() -> store.size(KEY)).isInstanceOf(IOException.class);
    }

    @Test
    void deletingIsIdempotent() throws IOException {
        BlobStore store = store();
        write(store, KEY, CONTENT);

        store.delete(KEY);
        store.delete(KEY);

        assertThat(store.exists(KEY)).isFalse();
    }

    /** 按前缀删：一个作业的全部文件一次清掉，**不碰**名字相邻的别的作业。 */
    @Test
    void deletingAPrefixRemovesEverythingUnderItAndNothingElse() throws IOException {
        BlobStore store = store();
        write(store, "t1/job-1/export.zip", CONTENT);
        write(store, "t1/job-1/att/aa11", CONTENT);
        write(store, "t1/job-1/att/aa11.gone", CONTENT);
        write(store, "t1/job-2/export.zip", CONTENT);
        write(store, "t2/job-1/export.zip", CONTENT);

        store.deletePrefix("t1/job-1");

        assertThat(store.exists("t1/job-1/export.zip")).isFalse();
        assertThat(store.exists("t1/job-1/att/aa11")).isFalse();
        assertThat(store.exists("t1/job-1/att/aa11.gone")).isFalse();
        assertThat(store.exists("t1/job-2/export.zip")).isTrue();
        assertThat(store.exists("t2/job-1/export.zip")).isTrue();
    }

    @Test
    void deletingAPrefixThatIsNotThereIsFine() throws IOException {
        BlobStore store = store();

        store.deletePrefix("t1/job-9");

        assertThat(store.exists("t1/job-9/export.zip")).isFalse();
    }

    /** 键一律由平台生成，从不含调用方文本；语法这一关两个实现都要过。 */
    @Test
    void rejectsKeysOutsideTheGrammar() throws IOException {
        BlobStore store = store();
        for (String bad : new String[] {"/leading", "UPPER/case", "a//b", "a/../b", "..", "", "a b/c",
                "a/" + "x".repeat(200)}) {
            assertThatThrownBy(() -> store.create(bad)).as(bad).isInstanceOf(IllegalArgumentException.class);
            assertThatThrownBy(() -> store.open(bad)).as(bad).isInstanceOf(IllegalArgumentException.class);
        }
    }

    /** 大一点的对象也是一样的：写出与读回都不把整份攒在内存里，但行为不变。 */
    @Test
    void handlesAnObjectLargerThanOneBuffer() throws IOException {
        BlobStore store = store();
        String big = "x".repeat(300_000);

        write(store, KEY, big);

        assertThat(store.size(KEY)).isEqualTo(300_000);
        assertThat(read(store, KEY)).isEqualTo(big);
    }
}
