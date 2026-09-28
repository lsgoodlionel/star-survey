package cn.mjy.platform.contacts;

/**
 * 引擎侧没有确认撤销，因此平台**没有**把这条映射标记为已撤销（ADR 0016）。
 *
 * <p>对外 503：这是"现在做不到、稍后重试"，不是"请求有问题"。措辞必须让调用方
 * 明白**邀请码此刻仍然有效**——把它说成一般性失败，撤销的人会以为已经生效。
 * 消息里不含令牌。
 */
public class InvitationRevocationFailedException extends RuntimeException {

    public InvitationRevocationFailedException(String message) {
        super(message);
    }
}
