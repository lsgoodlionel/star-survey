package cn.mjy.platform.asset;

import cn.mjy.platform.shared.storage.BlobStore;

/**
 * 资产字节的存储。契约全部来自 {@link BlobStore}——与导出文件共用同一个对象存储抽象
 * （ADR 0019 决定 2），这里只保留一个类型名，让两个模块各自注入到自己的那一份配置上。
 *
 * <p>落在哪里由共用的 {@code platform.storage} 一个开关决定（本地目录还是对象存储，
 * ADR 0015 增补五），与导出文件同进同退——**切不了一半**。
 */
interface AssetFileStore extends BlobStore {
}
