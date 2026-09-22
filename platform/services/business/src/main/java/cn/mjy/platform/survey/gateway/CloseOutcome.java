package cn.mjy.platform.survey.gateway;

/**
 * 收口的结局：{@link Closed}（200，引擎回读确认已过期）或 {@link NotClosed}（其他一切，包括超时与网络错误）。
 * NotClosed 只意味着"还不能确认已收口"，稍后重试即可。
 */
public sealed interface CloseOutcome {

    /** expires 是引擎里此刻的过期值；alreadyClosed 为真表示调用前就已过期。 */
    record Closed(String expires, boolean alreadyClosed) implements CloseOutcome {
    }

    record NotClosed(String reason) implements CloseOutcome {
    }
}
