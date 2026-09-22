package cn.mjy.platform.survey.gateway;

/**
 * 一次网关调用的结局，按"引擎到底有没有被改动"归为四类：
 * <ul>
 *   <li>{@link Published}：200，已激活；</li>
 *   <li>{@link Failed}：422（校验未过，引擎未触碰）或 502（引擎侧失败，已回滚或留下孤儿问卷）；</li>
 *   <li>{@link Refused}：400/401/404，网关拒收请求，引擎未触碰；</li>
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

    record Unknown(String reason) implements GatewayOutcome {
    }
}
