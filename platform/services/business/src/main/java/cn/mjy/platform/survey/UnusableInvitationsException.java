package cn.mjy.platform.survey;

/**
 * 发布回执里的邀请码本身不可用，<b>重试必然同样失败</b>（WP-18 / WP-05 接缝）。
 *
 * <p>网关按 {@code requestId} 存档回执，核对重发拿回的是同一份。所以"回执认不出的 {@code ref}"
 * "同一个人来了两条""两个人共用一个码"这类问题，重试多少次结论都一样——它们是<b>确定失败</b>，
 * 归 {@code publish_failed}，而不是"结果未知"的待核对：后者会让这份问卷永远卡在那儿。
 *
 * <p>真正可能是瞬时的（数据库不可用之类）不应该抛本异常，让它按待核对处理，下次核对自然就过去了。
 */
public class UnusableInvitationsException extends RuntimeException {

    public UnusableInvitationsException(String message) {
        super(message);
    }

    public UnusableInvitationsException(String message, Throwable cause) {
        super(message, cause);
    }
}
