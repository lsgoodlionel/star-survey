package cn.mjy.platform.response;

import java.util.List;

/**
 * 按需读取答卷作答值（ADR 0013 决定 1）。生产实现经发布网关的读端点调用引擎，
 * 平台自己从不持有引擎口令。调用方负责只传本租户已发布版本里记录的 (实例, sid)。
 */
public interface ResponseAnswerSource {

    /**
     * 读取指定答卷的指定列。引擎里已不存在的答卷不出现在结果里。
     *
     * @throws ResponseAnswersUnavailableException 网关未配置、不可达、拒绝或应答不可信
     */
    AnswerBatch read(String engineInstanceId, long engineSid, List<Long> responseIds, List<String> fieldnames);
}
