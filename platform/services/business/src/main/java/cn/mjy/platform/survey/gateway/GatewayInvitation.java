package cn.mjy.platform.survey.gateway;

/**
 * 发布回执里的一条邀请码（契约 publish-gateway-v1「邀请码回读」，ADR 0016 缺口 (a)）。
 *
 * <p>{@code index} 是定义里 {@code participants} 的下标，{@code ref} 是平台自己写进去、网关原样回显的
 * 不透明引用（本平台用联系人 id），{@code token} 是引擎签发的邀请码。
 *
 * <p><b>token 是能直接进入问卷的凭据</b>：只在发布收尾的事务里经过一次，随即写进
 * {@code contact_participation}，不进日志、不进审计、不进发布尝试或已发布版本的快照。
 */
public record GatewayInvitation(int index, String ref, String token) {
}
