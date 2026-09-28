package cn.mjy.platform.survey.gateway;

/**
 * 邀请码撤销的结局（契约 publish-gateway-v1.4，ADR 0016）。
 *
 * <p>只有 {@link Revoked} 才表示"这个码再也打不开问卷"。其余一切——引擎失败、超时、网络错误、
 * 网关未配置——都是 {@link NotRevoked}，含义是**结论未知**。平台在 NotRevoked 时绝不能写下
 * {@code revoked_at}：那正是这个缺口的本体，「平台以为撤销了，令牌照样能开问卷」。
 */
public sealed interface RevokeOutcome {

    /**
     * 引擎里那一行参与者已经不存在了。
     *
     * @param alreadyAbsent 调用前引擎里就没有这个码（幂等重试，或有人已在引擎侧删过）；
     *     结论同样是"进不去"，只是本次什么都没删
     */
    record Revoked(boolean alreadyAbsent) implements RevokeOutcome {
    }

    /** 没能确认撤销。reason 只用于日志与审计，**不含令牌**。 */
    record NotRevoked(String reason) implements RevokeOutcome {
    }
}
