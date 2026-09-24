package cn.mjy.platform.asset;

import java.util.UUID;

/**
 * 清单里的一条作答者上传（<b>不含字节</b>）。全部来自插件的上传会话表（ADR 0019 决定 8）。
 *
 * @param uploadToken  上传会话的 token，<b>即平台侧的资产 id</b>
 * @param responseId   哪份答卷
 * @param questionCode 哪道题
 * @param originalName 原始文件名，只作为元数据留档
 * @param extension    扩展名，同上
 * @param sizeBytes    引擎记下的字节数，只是线索——平台以实际取回的字节为准
 */
public record ResponderUploadRef(UUID uploadToken, long responseId, String questionCode, String originalName,
        String extension, long sizeBytes) {
}
