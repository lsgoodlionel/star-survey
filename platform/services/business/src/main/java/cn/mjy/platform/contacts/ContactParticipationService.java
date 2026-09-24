package cn.mjy.platform.contacts;

import cn.mjy.platform.shared.TenantContext;
import cn.mjy.platform.shared.tenant.TenantScope;
import cn.mjy.platform.survey.PublishedVersionView;
import cn.mjy.platform.survey.SurveyAccessDeniedException;
import cn.mjy.platform.survey.SurveyNotFoundException;
import cn.mjy.platform.survey.SurveyParticipantSource.Target;
import cn.mjy.platform.survey.SurveyService;
import java.util.Comparator;
import java.util.List;
import java.util.Optional;
import java.util.UUID;
import java.util.regex.Pattern;
import org.springframework.dao.DuplicateKeyException;
import org.springframework.stereotype.Service;

/**
 * 联系人 ↔ 引擎参与者令牌（WP-18 切片 18.1 的投放接点）。
 *
 * <p>平台不在这里向引擎发码：令牌由发布后从引擎 {@code tokens_<sid>} 读回（ADR 0016 缺口 (a)）。
 * 登记有两条路：
 * <ul>
 *   <li>{@link #linkIssued} —— <b>发布收尾自动登记</b>（本车道），按发布回执里的 {@code ref} 逐条写入。
 *       受众在固化定义时就已按发布者的数据范围判定过，收尾不再重判，后台核对用系统身份收尾时也照样登记；</li>
 *   <li>{@link #link} —— 人工登记，按调用者的权限与数据范围判定。</li>
 * </ul>
 * 两条路都保证两件事：令牌落在当前在线的已发布版本上；一个令牌同时只属于一个联系人（"续答令牌不互用"）。
 * <b>发送不在本模块。</b>
 */
@Service
public class ContactParticipationService {

    private static final Pattern TOKEN = Pattern.compile("[A-Za-z0-9_-]{4,64}");
    private static final int MAX_PAGE = 500;

    private final TenantScope tenantScope;
    private final ContactAccess access;
    private final ContactRepository contacts;
    private final ContactParticipationRepository participations;
    private final SurveyService surveys;
    private final ContactsAudit audit;

    ContactParticipationService(TenantScope tenantScope, ContactAccess access, ContactRepository contacts,
            ContactParticipationRepository participations, SurveyService surveys, ContactsAudit audit) {
        this.tenantScope = tenantScope;
        this.access = access;
        this.contacts = contacts;
        this.participations = participations;
        this.surveys = surveys;
        this.audit = audit;
    }

    /** 人工登记映射；同一联系人、同一版本、同一令牌重复登记幂等。 */
    public ParticipationView link(TenantContext ctx, UUID contactId, UUID surveyId, String participantToken) {
        String token = requireToken(participantToken);
        PublishedVersionView version = liveVersion(ctx, surveyId);
        Target target = new Target(version.version(), version.engineInstanceId(), version.engineSid());
        return tenantScope.call(ctx.tenantId(), () -> {
            requireVisible(ctx, contactId);
            return record(ctx, contactId, surveyId, target, token);
        });
    }

    /**
     * 发布收尾自动登记（WP-18 / WP-05 接缝）：邀请码是刚刚那次发布的产物，联系人 id 是平台自己写进
     * 定义、由网关原样回显的 {@code ref}。
     *
     * <p>这里<b>不重判</b>调用者的通讯录权限与数据范围：授权发生在固化定义、把受众物化成参与者的那一刻
     * （{@code SurveyAudienceService.recipients} 按发布者的范围取人）。收尾可能由后台核对以系统身份
     * 完成同一次发布，再判一次只会让那份问卷永远卡在待核对，而邀请码早已在引擎里签发。
     *
     * <p>必须与发布收尾同事务调用：登记不下去就整笔回滚、转待核对，重试时网关按 requestId 原样返回
     * 同一份回执，邀请码不会丢。
     *
     * <p>版本与引擎问卷由调用方传进来（{@code target}）：发布收尾刚写完这一版，不必每个邀请码
     * 再查一次已发布版本——那条查询会把每一版的完整定义反序列化一遍，而这一切都在问卷行锁里。
     *
     * <p>故意只对本包可见：唯一的调用方是 {@link ContactSurveyParticipants}（问卷模块经
     * {@code SurveyParticipantSource} 接缝调它），模块外拿不到这条跳过数据范围判定的入口。
     */
    ParticipationView linkIssued(TenantContext ctx, UUID contactId, UUID surveyId, Target target,
            String participantToken) {
        String token = requireToken(participantToken);
        return tenantScope.call(ctx.tenantId(), () -> {
            if (!contacts.exists(contactId)) {
                throw new ContactNotFoundException("contact not found: " + contactId);
            }
            return record(ctx, contactId, surveyId, target, token);
        });
    }

    /** 写入一条映射（已在租户作用域内、已判定过授权）；重复登记同一令牌幂等。 */
    private ParticipationView record(TenantContext ctx, UUID contactId, UUID surveyId, Target target, String token) {
        Optional<ParticipationView> current = participations.findCurrent(contactId, surveyId, target.versionNo());
        if (current.isPresent()) {
            if (current.get().participantToken().equals(token)) {
                return current.get();
            }
            throw new ContactConflictException("participation_exists",
                    "contact " + contactId + " already holds a token for version " + target.versionNo());
        }
        ParticipationView linked;
        try {
            linked = participations.insert(UUID.randomUUID(), contactId, surveyId, target.versionNo(),
                    target.engineInstanceId(), target.engineSid(), token, ctx.actorId());
        } catch (DuplicateKeyException e) {
            throw new ContactConflictException("token_taken",
                    "this participant token is already mapped to another contact");
        }
        // 审计里只有 UUID 与版本号：令牌是能直接进入问卷的凭据，绝不进审计或日志。
        audit.record(ctx, ContactsAudit.PARTICIPATION_LINK, "contact", contactId,
                "survey/" + surveyId + "/v" + target.versionNo());
        return linked;
    }

    private static String requireToken(String participantToken) {
        String token = DedupeKey.trim(participantToken);
        if (token == null || !TOKEN.matcher(token).matches()) {
            throw new InvalidContactRequestException("participantToken must match [A-Za-z0-9_-]{4,64}");
        }
        return token;
    }

    public Optional<ParticipationView> current(TenantContext ctx, UUID contactId, UUID surveyId) {
        PublishedVersionView version = liveVersion(ctx, surveyId);
        return tenantScope.call(ctx.tenantId(), () -> {
            requireVisible(ctx, contactId);
            return participations.findCurrent(contactId, surveyId, version.version());
        });
    }

    /** 某一版问卷上的全部有效映射，供投放车道成批取用。 */
    public List<ParticipationView> forSurvey(TenantContext ctx, UUID surveyId, int limit) {
        PublishedVersionView version = liveVersion(ctx, surveyId);
        int page = Math.min(Math.max(limit, 1), MAX_PAGE);
        return tenantScope.call(ctx.tenantId(), () -> {
            access.scopeOf(ctx);
            return participations.forSurvey(surveyId, version.version(), page);
        });
    }

    public void revoke(TenantContext ctx, UUID participationId) {
        tenantScope.run(ctx.tenantId(), () -> {
            ContactScope scope = access.scopeOf(ctx);
            ParticipationView participation = participations.find(participationId)
                    .orElseThrow(() -> new ContactNotFoundException("participation not found: " + participationId));
            if (contacts.findVisible(scope, participation.contactId()).isEmpty()) {
                throw new ContactNotFoundException("participation not found: " + participationId);
            }
            if (participations.revoke(participationId, ctx.actorId())) {
                audit.record(ctx, ContactsAudit.PARTICIPATION_REVOKE, "contact-participation", participationId);
            }
        });
    }

    /** 当前在线的已发布版本；问卷不存在、无权或尚未发布都等同"没有参与者令牌可发"。 */
    private PublishedVersionView liveVersion(TenantContext ctx, UUID surveyId) {
        if (surveyId == null) {
            throw new InvalidContactRequestException("surveyId is required");
        }
        List<PublishedVersionView> versions;
        try {
            versions = surveys.versions(ctx, surveyId);
        } catch (SurveyNotFoundException e) {
            throw new ContactNotFoundException("survey not found: " + surveyId);
        } catch (SurveyAccessDeniedException e) {
            throw new ContactAccessDeniedException(e.reason(), e.getMessage());
        }
        return versions.stream().filter(PublishedVersionView::live)
                .max(Comparator.comparingInt(PublishedVersionView::version))
                .orElseThrow(() -> new ContactNotFoundException("survey has no live published version: " + surveyId));
    }

    private void requireVisible(TenantContext ctx, UUID contactId) {
        ContactScope scope = access.scopeOf(ctx);
        if (contacts.findVisible(scope, contactId).isEmpty()) {
            throw new ContactNotFoundException("contact not found: " + contactId);
        }
    }
}
