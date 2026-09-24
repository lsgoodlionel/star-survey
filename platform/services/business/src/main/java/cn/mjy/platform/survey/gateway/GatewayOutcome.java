package cn.mjy.platform.survey.gateway;

/**
 * 一次网关调用的结局，按"引擎到底有没有被改动"归为五类：
 * <ul>
 *   <li>{@link Published}：200，已激活；</li>
 *   <li>{@link Failed}：422（校验未过，引擎未触碰）或 502（引擎侧失败，已回滚或留下孤儿问卷）；</li>
 *   <li>{@link Refused}：400/401/404，网关拒收请求，引擎未触碰；</li>
 *   <li>{@link Expired}：410，网关的回执已过留存期（契约 v1.3）。重发只会再得到 410，
 *       所以这是<b>终局</b>而不是"结果未知"；引擎里可能还留着一份问卷，需要人工清理。
 *       带过邀请码的回执按更短的窗口过期（缺省 24 小时），此时那批码也已经不在了；</li>
 *   <li>{@link Unknown}：超时、网络错误、409 发布进行中、应答不可解析或其他意外状态——结果未知，
 *       只能用同一 requestId 重试，靠网关幂等拿到确定结果。</li>
 * </ul>
 */
public sealed interface GatewayOutcome {

    record Published(GatewayResult result) implements GatewayOutcome {
    }

    record Failed(int httpStatus, GatewayResult result) implements GatewayOutcome {
    }

    record Refused(int httpStatus, String error) implements GatewayOutcome {
    }

    /**
     * @param originalStatus       首次应答的 HTTP 状态（读不出来时为 0）
     * @param surveyId             首次发布在引擎里留下的问卷（发布成功、或回滚也失败时才有）
     * @param createdAt            首次结果落库的时刻（UTC，读不出来时为 null）
     * @param heldInvitationCodes  这份回执带过邀请码。为真时引擎里那份问卷**已经签发过码**，
     *                             而平台没能收下它们——重新发布会换一批新码，旧码随旧 sid 作废
     */
    record Expired(int originalStatus, Integer surveyId, String createdAt, boolean heldInvitationCodes)
            implements GatewayOutcome {
    }

    record Unknown(String reason) implements GatewayOutcome {
    }
}
