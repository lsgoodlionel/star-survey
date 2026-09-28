package cn.mjy.platform.asset;

import java.util.List;
import java.util.UUID;

/**
 * 「作答者上传从哪来」这条跨进程接缝（ADR 0019 决定 8）。
 *
 * <p>生产实现是 {@link HttpResponderUploadSource}（经网关问插件）；用例注入假实现。
 * 两个方法都<b>失败即关闭</b>：读不到一律抛 {@link ResponderUploadsUnavailableException}，
 * 绝不返回空当作"这份答卷没有上传"。
 */
public interface ResponderUploadSource {

    /** 一页答卷的上传清单（不含字节）。 */
    List<ResponderUploadRef> manifest(String engineInstanceId, long engineSid, String generation,
            List<Long> responseIds);

    /** 一件上传的字节。 */
    ResponderUploadBytes content(String engineInstanceId, long engineSid, String generation, UUID uploadToken);
}
