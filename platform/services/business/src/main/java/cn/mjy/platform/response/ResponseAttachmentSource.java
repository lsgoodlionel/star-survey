package cn.mjy.platform.response;

import java.io.OutputStream;

/**
 * 按需取一份附件的字节（ADR 0015 增补四）。生产实现经发布网关的取件端点调用引擎插件通道，
 * 平台自己从不持有引擎口令，也从不直接与插件通信。
 *
 * <p>一次一份、流式写进 {@code sink}：整份答卷的附件永远不会同时在内存里
 * （ADR 0015 决定 2 的"内存有界"在附件这一侧的落法）。
 */
public interface ResponseAttachmentSource {

    /** 一次取件的结局。这两种都是<b>永久</b>结论；暂时性失败由异常表示，会被重试。 */
    enum Outcome {
        /** 字节已经完整写进 sink。 */
        STORED,
        /** 引擎里已经没有这一份（答卷被删、代次不符、这一列里没有这个存储名、文件已清理）。不重试。 */
        NOT_FOUND,
        /** 超出单份上限。不重试，也不写半截文件。 */
        TOO_LARGE
    }

    /**
     * @param sink 只写、不关闭；返回 {@link Outcome#STORED} 之外的结局时一个字节都不写
     * @throws ResponseAttachmentsUnavailableException 网关未配置、不可达、拒绝或应答不可信（暂时失败，可重试）
     */
    Outcome fetch(AttachmentQuery query, OutputStream sink);
}
