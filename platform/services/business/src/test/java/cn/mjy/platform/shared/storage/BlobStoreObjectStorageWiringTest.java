package cn.mjy.platform.shared.storage;

import static cn.mjy.platform.shared.storage.StoreBeans.delegate;
import static org.assertj.core.api.Assertions.assertThat;

import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.context.ApplicationContext;
import org.springframework.test.context.TestPropertySource;

/**
 * 配上对象存储时两处**一起**切过去，且在同一个桶里各占一个键前缀。
 *
 * <p>这是「两处共用一套存储」整件事的验收点：没有它，"切到对象存储"就可能只切了一半
 * （导出过去了、资产还在本地卷上），而那正是两条遗留各自为政的样子。
 * 缺省落本地的那一半在 {@link BlobStoreWiringTest}；不用 {@code @Nested} 把两者合成一个类的
 * 理由写在那一个的类注释里。
 */
@SpringBootTest
@TestPropertySource(properties = {
    "platform.storage.kind=s3",
    "platform.storage.endpoint=http://127.0.0.1:1/",
    "platform.storage.region=cn-test-1",
    "platform.storage.bucket=mjy",
    "platform.storage.access-key-id=AKIAEXAMPLEEXAMPLE",
    "platform.storage.secret-access-key=secret-secret-secret",
})
class BlobStoreObjectStorageWiringTest {

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
