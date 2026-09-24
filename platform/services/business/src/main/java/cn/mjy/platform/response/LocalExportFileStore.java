package cn.mjy.platform.response;

import cn.mjy.platform.shared.storage.LocalBlobStore;
import java.nio.file.Path;

/**
 * 导出文件的本地实现：机制全在 {@link LocalBlobStore}（写临时文件再原子改名、拒绝目录穿越），
 * 这里只把它接到导出模块的类型上。生产换对象存储时换的是 {@link LocalBlobStore} 的兄弟实现，
 * 导出与资产一起受益。
 */
final class LocalExportFileStore extends LocalBlobStore implements ExportFileStore {

    LocalExportFileStore(Path root) {
        super(root);
    }
}
