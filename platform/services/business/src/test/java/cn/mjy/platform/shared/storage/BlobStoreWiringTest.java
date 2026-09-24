package cn.mjy.platform.shared.storage;

import static cn.mjy.platform.shared.storage.StoreBeans.delegate;
import static org.assertj.core.api.Assertions.assertThat;

import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.context.ApplicationContext;

/**
 * 缺省接线：导出与资产都落本地实现——既有部署与全部测试一个字都不用改。
 * 换成对象存储的那一半在 {@link BlobStoreObjectStorageWiringTest}（它要另一套属性、
 * 因而是另一个 Spring 上下文）。
 *
 * <p><b>这两个类刻意不用 {@code @Nested} 合成一个。</b>surefire 的 XML 会把 {@code @Nested}
 * 的用例归到外层类名下，而同一次跑生成的纯文本摘要却给那个类记 {@code Tests run: 0}，
 * 且不为嵌套类另出文件。于是「跑后核对总数」这条防假绿的规矩，在按 {@code .txt} 计数时
 * 对这样的类**恰好失明**——它本来就记 0，哪天真的不跑了，总数纹丝不动。
 * 摊平成顶层类之后不再有「报告存在但计 0」的形状，用哪个 reporter 数都一样。
 */
@SpringBootTest
class BlobStoreWiringTest {

    @Autowired
    private ApplicationContext context;

    @Test
    void bothModulesFallBackToTheLocalFileSystem() {
        assertThat(delegate(context, "exportFileStore")).isInstanceOf(LocalBlobStore.class);
        assertThat(delegate(context, "assetFileStore")).isInstanceOf(LocalBlobStore.class);
    }
}
