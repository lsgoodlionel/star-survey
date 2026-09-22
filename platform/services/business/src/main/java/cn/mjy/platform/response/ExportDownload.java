package cn.mjy.platform.response;

import java.io.InputStream;

/** 一次已通过再次授权的下载：调用方负责关闭 content。 */
public record ExportDownload(String fileName, String contentType, long size, String sha256, InputStream content) {
}
