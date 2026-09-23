package cn.mjy.platform.response;

import java.util.List;
import java.util.Map;

/**
 * 读取"每份答卷是用哪个邀请码答的"（契约 response-read-v1「参与者令牌」，ADR 0016 缺口 (b)）。
 *
 * <p>与 {@link ResponseAnswerSource} 分开，是因为调用方不同、要的东西也不同：对账只要身份、
 * 不要任何作答值，读端点为此允许 {@code fields} 为空。
 *
 * <p><b>认不出是谁的答卷不出现在结果里</b>：匿名问卷网关一律回 null，没用邀请码进场的答卷也是——
 * 都不能当作某个人答的。
 */
public interface RespondentSource {

    /**
     * @return 答卷号 → 参与者令牌，只含确实认得出的那些
     * @throws ResponseAnswersUnavailableException 网关未配置、不可达、拒绝或应答不可信
     */
    Map<Long, String> readRespondents(String engineInstanceId, long engineSid, List<Long> responseIds);
}
