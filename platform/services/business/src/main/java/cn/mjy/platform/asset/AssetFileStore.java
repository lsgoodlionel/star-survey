package cn.mjy.platform.asset;

import cn.mjy.platform.shared.storage.BlobStore;

/**
 * 资产字节的存储。契约全部来自 {@link BlobStore}——与导出文件共用同一个对象存储抽象
 * （ADR 0019 决定 2），这里只保留一个类型名，让两个模块各自注入到自己的那一份配置上。
 */
interface AssetFileStore extends BlobStore {
}
