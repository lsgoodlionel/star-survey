package cn.mjy.platform.survey;

import cn.mjy.platform.shared.TenantContext;
import cn.mjy.platform.survey.gateway.GatewayInvitation;
import java.util.List;
import java.util.UUID;

/**
 * 「谁该拿到这份问卷的邀请码」这条跨模块接缝（WP-18 / WP-05，ADR 0016 缺口 (a) 的上游）。
 *
 * <p>为什么需要它：问卷模块只管发布，不认识联系人；通讯录模块管着联系人、部门树与数据范围，
 * 却不参与发布。这个接口把两件事接起来——发布前把受众<b>物化</b>成定义里的 {@code participants}，
 * 发布成功后按回执里的 {@code ref} 把邀请码<b>登记</b>回联系人。实现在
 * {@code cn.mjy.platform.contacts}，问卷模块只依赖本接口。
 *
 * <p>没有注册实现时（通讯录模块未装）等同"谁也没选"：需要邀请码的问卷因此发不出去，
 * 而不是发出一份谁都进不去的问卷。
 */
public interface SurveyParticipantSource {

    /**
     * 这批邀请码要登记到哪一版问卷、哪份引擎问卷上。发布收尾刚刚写完这一版，三个值都已在手，
     * 因此直接传下来——否则每个邀请码都要再查一次已发布版本，而那条查询会把每一版的完整定义
     * （正好嵌着这批参与者）反序列化一遍，全都发生在持着问卷行锁的事务里。
     */
    record Target(int versionNo, String engineInstanceId, int engineSid) {
    }

    /**
     * 本次发布该邀请谁，按调用者当时的数据范围求得，顺序稳定。
     *
     * @return 联系人 id，用作定义里 {@code participants[].ref}；一个都没有时返回空表
     */
    List<UUID> audienceOf(TenantContext ctx, UUID surveyId);

    /**
     * 按回执里的 {@code ref} 把邀请码登记成"某个联系人在某一版问卷上的令牌"。
     *
     * <p>在发布收尾的同一个事务里调用：登记不下去就整笔回滚、转为待核对，用同一 requestId 重试时
     * 网关会原样返回存档的回执，邀请码不会丢。绝不允许"路由登记了，却没人知道码发给了谁"。
     *
     * @throws UnusableInvitationsException 回执本身不可用（认不出的 ref、同一个人两条、两人共用一个码）：
     *     重试必然同样失败，调用方应据此判<b>确定失败</b>而不是待核对
     */
    void registerInvitations(TenantContext ctx, UUID surveyId, Target target, List<GatewayInvitation> invitations);
}
