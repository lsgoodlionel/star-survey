package cn.mjy.platform.shared.storage;

import org.springframework.context.ApplicationContext;

/**
 * 接线测试共用的一行：两个模块的存储 bean 都是同一个转发壳，
 * 壳里装的那一个才是按 {@code platform.storage} 选出来的实现。
 *
 * <p>本身不带任何用例，只是被 {@link BlobStoreWiringTest} 与
 * {@link BlobStoreObjectStorageWiringTest} 共用——那两个类各自跑在不同的 Spring 上下文里，
 * 不能靠继承或 {@code @Nested} 共享（见它们类注释里那条「不用 @Nested」的理由）。
 */
final class StoreBeans {

    private StoreBeans() {
    }

    static BlobStore delegate(ApplicationContext context, String beanName) {
        return ((DelegatingBlobStore) context.getBean(beanName)).delegate();
    }
}
