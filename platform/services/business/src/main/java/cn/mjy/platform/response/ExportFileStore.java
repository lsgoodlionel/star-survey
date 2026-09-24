package cn.mjy.platform.response;

import cn.mjy.platform.shared.storage.BlobStore;

/**
 * 导出文件的存储。契约全部来自 {@link BlobStore}——导出与资产共用同一个对象存储抽象
 * （ADR 0019 决定 2），这里只保留导出模块自己的类型名，不加任何成员。
 */
public interface ExportFileStore extends BlobStore {
}
