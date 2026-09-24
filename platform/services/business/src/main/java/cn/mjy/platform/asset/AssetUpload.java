package cn.mjy.platform.asset;

/**
 * 一次上传的内容。{@code contentType} 是调用方<b>声明</b>的类型，只用来和嗅探结果对照；
 * {@code originalName} 只作为元数据留档，绝不参与存储键。
 *
 * <p>{@code content} 不做防御性复制：字节数组由调用方一次性交出、之后不再持有，
 * 为一次上传复制一份 16 MiB 只是浪费。
 */
public record AssetUpload(String contentType, String originalName, byte[] content) {
}
