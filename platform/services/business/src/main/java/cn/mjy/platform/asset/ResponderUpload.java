package cn.mjy.platform.asset;

import java.util.UUID;

/**
 * 一次作答者上传的全部事实，<b>全部来自上传会话</b>
 * （{@code MjyUploadSessionStore}，ADR 0006 决定 4 / ADR 0019 决定 8）。
 *
 * <p>答卷字段里那张文件清单一个字都不参与：它是作答者可控的 POST 数据，
 * 而会话行是引擎在 {@code beforeProcessFileUpload} 里自己写的。清单顶多是线索，不是事实。
 *
 * @param uploadToken      上传会话的 {@code upload_token}，<b>即平台侧的资产 id</b>
 * @param engineInstanceId 哪台引擎
 * @param engineSid        引擎侧问卷号
 * @param generation       代次（ADR 0012）
 * @param responseId       答卷号
 * @param questionCode     题目代码
 * @param respondentToken  参与者令牌；<b>只用来算指纹，绝不落库</b>。匿名作答时为 {@code null}
 * @param contentType      引擎记下的声明类型，只用来和嗅探结果对照
 * @param originalName     原始文件名，只作为元数据留档，绝不参与存储键
 * @param content          字节
 */
public record ResponderUpload(
        UUID uploadToken,
        String engineInstanceId,
        long engineSid,
        String generation,
        long responseId,
        String questionCode,
        String respondentToken,
        String contentType,
        String originalName,
        byte[] content) {

    public boolean hasRespondentToken() {
        return respondentToken != null && !respondentToken.isBlank();
    }
}
