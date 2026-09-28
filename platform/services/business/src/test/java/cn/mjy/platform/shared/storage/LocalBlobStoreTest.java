package cn.mjy.platform.shared.storage;

import static org.assertj.core.api.Assertions.assertThatThrownBy;

import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

/** 本地文件系统实现要满足与对象存储实现**同一份**契约（开发与测试用，多副本需共享卷）。 */
class LocalBlobStoreTest extends BlobStoreContractTest {

    @TempDir
    private Path root;

    @Override
    protected BlobStore store() {
        return new LocalBlobStore(root);
    }

    /** 键的语法之外再挡一次目录穿越：字符集将来放宽时这一道仍然成立。 */
    @Test
    void neverResolvesOutsideTheRoot() {
        LocalBlobStore store = new LocalBlobStore(root);

        assertThatThrownBy(() -> store.open("../outside")).isInstanceOf(IllegalArgumentException.class);
    }

    /** 放弃的上传不留临时文件。 */
    @Test
    void anAbandonedUploadLeavesNoTemporaryFileBehind() throws IOException {
        LocalBlobStore store = new LocalBlobStore(root);

        try (BlobStore.Upload upload = store.create("t1/a")) {
            upload.stream().write("x".getBytes());
        }

        try (var files = Files.walk(root)) {
            org.assertj.core.api.Assertions.assertThat(files.filter(Files::isRegularFile)).isEmpty();
        }
    }
}
