package cn.mjy.platform.response;

import java.util.Objects;

/**
 * 取一份附件字节的请求（契约 response-read-v1「附件取件」，ADR 0015 增补四）。
 *
 * <p>只按**引擎生成的存储名**取件：作答者起的原始文件名不进请求，因此不会出现在任何一段访问日志里
 * （ADR 0018 决定 1 的同一条理由——元数据可以进 URL，作答内容不行）。
 *
 * @param generation 代次；附件只对创建作业时的最近代次取，串代次一律取不到
 * @param fieldname  引擎答卷表里那一列的列名：插件据此核对这份文件确实属于这份答卷的这一列
 * @param storedName 引擎给这份文件起的存储名
 * @param maxBytes   单份上限，超出即判 {@link ResponseAttachmentSource.Outcome#TOO_LARGE}，不写半截文件
 */
public record AttachmentQuery(String engineInstanceId, long engineSid, String generation, long responseId,
        String fieldname, String storedName, long maxBytes) {

    public AttachmentQuery {
        Objects.requireNonNull(engineInstanceId, "engineInstanceId");
        if (generation == null || generation.isBlank()) {
            throw new IllegalArgumentException("an attachment belongs to a generation");
        }
        if (fieldname == null || fieldname.isBlank() || storedName == null || storedName.isBlank()) {
            throw new IllegalArgumentException("an attachment is named by its column and stored name");
        }
        if (responseId <= 0 || engineSid <= 0 || maxBytes <= 0) {
            throw new IllegalArgumentException("attachment query bounds must be positive");
        }
    }
}
