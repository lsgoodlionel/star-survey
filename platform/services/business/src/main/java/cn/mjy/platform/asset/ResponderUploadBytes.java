package cn.mjy.platform.asset;

/** 一件作答者上传的字节及其清单条目。 */
public record ResponderUploadBytes(ResponderUploadRef ref, byte[] content) {
}
