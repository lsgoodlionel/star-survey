package cn.mjy.platform.shared.storage;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import java.time.Duration;
import org.junit.jupiter.api.Nested;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.context.ApplicationContext;
import org.springframework.test.context.TestPropertySource;

/**
 * 两处共用一套存储：导出与资产由**同一份** {@code platform.storage} 配置决定落在哪里。
 *
 * <p>这两条断言是整件事的验收点——没有它们，"切到对象存储"就可能只切了一半
 * （导出过去了、资产还在本地卷上），而那正是两条遗留各自为政的样子。
 */
class BlobStoreWiringTest {

    /** 缺省仍是本地实现：既有部署与测试一个字都不用改。 */
    @SpringBootTest
    @Nested
    class DefaultsToLocal {

        @Autowired
        private ApplicationContext context;

        @Test
        void bothModulesFallBackToTheLocalFileSystem() {
            assertThat(delegate(context, "exportFileStore")).isInstanceOf(LocalBlobStore.class);
            assertThat(delegate(context, "assetFileStore")).isInstanceOf(LocalBlobStore.class);
        }
    }

    /** 配上对象存储时两处一起切过去，且在同一个桶里各占一个前缀。 */
    @SpringBootTest
    @TestPropertySource(properties = {
        "platform.storage.kind=s3",
        "platform.storage.endpoint=http://127.0.0.1:1/",
        "platform.storage.region=cn-test-1",
        "platform.storage.bucket=mjy",
        "platform.storage.access-key-id=AKIAEXAMPLEEXAMPLE",
        "platform.storage.secret-access-key=secret-secret-secret",
    })
    @Nested
    class ConfiguredForObjectStorage {

        @Autowired
        private ApplicationContext context;

        @Test
        void bothModulesSwitchTogetherAndKeepToTheirOwnPrefix() {
            BlobStore exports = delegate(context, "exportFileStore");
            BlobStore assets = delegate(context, "assetFileStore");

            assertThat(exports).isInstanceOf(S3BlobStore.class);
            assertThat(assets).isInstanceOf(S3BlobStore.class);
            assertThat(((S3BlobStore) exports).keyPrefix()).isEqualTo("exports/");
            assertThat(((S3BlobStore) assets).keyPrefix()).isEqualTo("assets/");
        }
    }

    /** 两个模块的存储 bean 都是同一个转发壳，壳里装的才是按配置选出来的实现。 */
    private static BlobStore delegate(ApplicationContext context, String beanName) {
        return ((DelegatingBlobStore) context.getBean(beanName)).delegate();
    }

    /** 配了一半的对象存储**拒绝启动**，绝不悄悄退回本地卷。 */
    @Test
    void halfConfiguredObjectStorageIsRefusedOutright() {
        assertThatThrownBy(() -> new BlobStoreProperties("s3", "http://oss", "cn-test-1", "", "ak", "sk", true,
                Duration.ofSeconds(10)))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("bucket");
        assertThatThrownBy(() -> new BlobStoreProperties("s3", "", "cn-test-1", "mjy", "ak", "sk", true,
                Duration.ofSeconds(10)))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("endpoint");
        assertThatThrownBy(() -> new BlobStoreProperties("s3", "http://oss", "cn-test-1", "mjy", "ak", "", true,
                Duration.ofSeconds(10)))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("secret-access-key");
    }

    @Test
    void anUnknownKindIsRefused() {
        assertThatThrownBy(() -> new BlobStoreProperties("oss", "", "cn-test-1", "", "", "", true,
                Duration.ofSeconds(10)))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("kind");
    }

    /** 端点末尾的斜杠一律去掉，拼路径时只加一次。 */
    @Test
    void theEndpointNeverKeepsATrailingSlash() {
        BlobStoreProperties properties = new BlobStoreProperties("s3", "http://oss.example.com//", "cn-test-1",
                "mjy", "ak", "sk", true, Duration.ofSeconds(10));

        assertThat(properties.endpoint()).isEqualTo("http://oss.example.com");
    }
}
