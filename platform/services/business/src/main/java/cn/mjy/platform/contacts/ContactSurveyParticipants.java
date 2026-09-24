package cn.mjy.platform.contacts;

import cn.mjy.platform.shared.TenantContext;
import cn.mjy.platform.survey.SurveyParticipantSource;
import cn.mjy.platform.survey.UnusableInvitationsException;
import cn.mjy.platform.survey.gateway.GatewayInvitation;
import java.util.List;
import java.util.UUID;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Component;

/**
 * 联系人名单 ↔ 发布参与者（WP-18 / WP-05 接缝，ADR 0016）。发布前把受众物化成参与者引用，
 * 发布成功后按回执里的 {@code ref} 自动登记映射（{@link ContactParticipationService#linkIssued}）——
 * WP-18 原本的"由投放车道手工登记"从此改成自动。
 *
 * <p><b>个人信息不出平台</b>：参与者条目上只有 {@code ref}＝联系人 id，姓名、邮箱、手机号一概不发给引擎。
 * 一来发布尝试与已发布版本的定义快照是不可变的，个人信息写进去就再也删不掉；二来平台自己发邀请
 * （投放车道按 {@link ContactParticipationService#forSurvey} 取映射、按联系人自己的地址发），
 * 引擎根本不需要知道收件人是谁。
 *
 * <p><b>邀请码只落一处</b>：{@code contact_participation.participant_token}。它是能直接进入问卷的凭据，
 * 因此不进审计、不进日志、不进定义快照——网关那边按 requestId 存档回执已经是一条已知遗留，不再多一处。
 */
@Component
class ContactSurveyParticipants implements SurveyParticipantSource {

    private static final Logger log = LoggerFactory.getLogger(ContactSurveyParticipants.class);

    private final SurveyAudienceService audiences;
    private final ContactParticipationService participations;

    ContactSurveyParticipants(SurveyAudienceService audiences, ContactParticipationService participations) {
        this.audiences = audiences;
        this.participations = participations;
    }

    @Override
    public List<UUID> audienceOf(TenantContext ctx, UUID surveyId) {
        return audiences.recipients(ctx, surveyId).stream().map(ContactView::id).toList();
    }

    @Override
    public void registerInvitations(TenantContext ctx, UUID surveyId, Target target,
            List<GatewayInvitation> invitations) {
        for (GatewayInvitation invitation : invitations) {
            UUID contactId = contactOf(invitation);
            try {
                participations.linkIssued(ctx, contactId, surveyId, target, invitation.token());
            } catch (InvalidContactRequestException | ContactConflictException | ContactNotFoundException e) {
                // 回执本身有问题（码不合法、同一个人来了两条、两人共用一个码）：重发拿回的是同一份
                // 存档回执，结论只会一样，所以是确定失败。消息里只有下标与联系人 id，绝不回显令牌。
                throw new UnusableInvitationsException("invitation " + invitation.index() + " of survey "
                        + surveyId + " cannot be mapped to contact " + contactId + ": " + e.getMessage(), e);
            }
        }
        log.info("mapped {} invitation codes to contacts on survey {} v{}",
                invitations.size(), surveyId, target.versionNo());
    }

    /**
     * ref 是平台自己写进定义的联系人 id，回执必须原样回显。认不出来就是确定失败：
     * 硬配一个联系人比登记不上更糟——邀请会发给错的人。
     */
    private UUID contactOf(GatewayInvitation invitation) {
        String ref = DedupeKey.trim(invitation.ref());
        if (ref == null) {
            throw new UnusableInvitationsException(
                    "invitation " + invitation.index() + " came back without the platform reference");
        }
        try {
            return UUID.fromString(ref);
        } catch (IllegalArgumentException e) {
            throw new UnusableInvitationsException(
                    "invitation " + invitation.index() + " came back with a reference the platform never issued", e);
        }
    }
}
