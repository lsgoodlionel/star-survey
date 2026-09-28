package cn.mjy.platform.shared.storage;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import java.time.Duration;
import org.junit.jupiter.api.Test;

/**
 * {@code platform.storage} 的取值判定。纯单元——它判的是构造器里的那几条，
 * 不需要 Spring 上下文，所以也不该为它起一个。
 */
class BlobStorePropertiesTest {

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
