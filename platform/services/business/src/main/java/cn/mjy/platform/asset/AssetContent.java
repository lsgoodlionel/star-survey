package cn.mjy.platform.asset;

import java.io.InputStream;

/** 回放一个版本的字节。{@code contentType} 是入库时嗅探出来的那个，不是当时声明的。 */
public record AssetContent(String contentType, long byteSize, String sha256, InputStream content) {
}
