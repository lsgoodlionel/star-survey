package cn.mjy.platform.contacts;

import cn.mjy.platform.shared.TenantContext;
import cn.mjy.platform.shared.tenant.TenantScope;
import cn.mjy.platform.survey.SurveyAccessDeniedException;
import cn.mjy.platform.survey.SurveyNotFoundException;
import cn.mjy.platform.survey.SurveyService;
import java.util.ArrayList;
import java.util.List;
import java.util.Optional;
import java.util.UUID;
import org.springframework.stereotype.Service;

/**
 * 问卷受众（WP-18 / WP-05 接缝）：一份问卷要邀请哪一批联系人。
 *
 * <p>存的是<b>条件</b>（名单 / 部门 / 标签的交集），发布时现算成参与者，因此名单改了下一版就跟着变，
 * 不需要维护第二份名册。至少要给一个条件：不给条件等于"全租户所有联系人"，一次误发布就会给整个租户发邀请。
 *
 * <p>算受众一律按<b>调用者当时的数据范围</b>（ADR 0017 决定 4）：发布者看不见的联系人不会被邀请，
 * 绝不因为"设受众的人看得见"就放宽。看不见就等于不存在，失败即关闭。
 */
@Service
public class SurveyAudienceService {

    /**
     * 一次发布最多物化多少个参与者。定义整体受网关 1 MiB 上限约束（{@code SurveyDefinitions}），
     * 一条只带 ref 的参与者约 48 字节，2000 条远在上限之内，同时也挡住"误选全租户"这种量级。
     */
    public static final int MAX_PARTICIPANTS = 2000;
    private static final int PAGE = 200;
    private static final int MAX_TAG = 64;

    private final TenantScope tenantScope;
    private final ContactAccess access;
    private final ContactListService lists;
    private final OrgUnitService units;
    private final ContactDirectoryService contacts;
    private final SurveyAudienceRepository audiences;
    private final SurveyService surveys;
    private final ContactsAudit audit;

    SurveyAudienceService(TenantScope tenantScope, ContactAccess access, ContactListService lists,
            OrgUnitService units, ContactDirectoryService contacts, SurveyAudienceRepository audiences,
            SurveyService surveys, ContactsAudit audit) {
        this.tenantScope = tenantScope;
        this.access = access;
        this.lists = lists;
        this.units = units;
        this.contacts = contacts;
        this.audiences = audiences;
        this.surveys = surveys;
        this.audit = audit;
    }

    /** 设定（或整条替换）受众。名单、部门都必须是调用者看得见的，问卷也是。 */
    public SurveyAudienceView set(TenantContext ctx, UUID surveyId, UUID listId, UUID orgUnitId,
            boolean includeDescendants, String tag) {
        String trimmedTag = DedupeKey.trim(tag);
        if (listId == null && orgUnitId == null && trimmedTag == null) {
            throw new InvalidContactRequestException("an audience needs at least one of listId, orgUnitId, tag");
        }
        if (trimmedTag != null && trimmedTag.length() > MAX_TAG) {
            throw new InvalidContactRequestException("tag must be at most " + MAX_TAG + " characters");
        }
        requireSurvey(ctx, surveyId);
        return tenantScope.call(ctx.tenantId(), () -> {
            ContactScope scope = access.scopeOf(ctx);
            if (listId != null) {
                lists.require(listId);
            }
            if (orgUnitId != null) {
                units.requireVisible(scope, orgUnitId);
            }
            SurveyAudienceView saved =
                    audiences.upsert(surveyId, listId, orgUnitId, includeDescendants, trimmedTag, ctx.actorId());
            audit.record(ctx, ContactsAudit.AUDIENCE_SET, "survey", surveyId,
                    "list=" + listId + " unit=" + orgUnitId + " tag=" + (trimmedTag == null ? "-" : "yes"));
            return saved;
        });
    }

    /** 读受众同样要两道判定：通讯录权限，以及这份问卷本身可不可见——否则就能按 surveyId 枚举出"谁被邀请了"。 */
    public Optional<SurveyAudienceView> find(TenantContext ctx, UUID surveyId) {
        requireSurvey(ctx, surveyId);
        return tenantScope.call(ctx.tenantId(), () -> {
            access.scopeOf(ctx);
            return audiences.find(surveyId);
        });
    }

    public void clear(TenantContext ctx, UUID surveyId) {
        requireSurvey(ctx, surveyId);
        tenantScope.run(ctx.tenantId(), () -> {
            access.scopeOf(ctx);
            if (audiences.delete(surveyId)) {
                audit.record(ctx, ContactsAudit.AUDIENCE_CLEAR, "survey", surveyId);
            }
        });
    }

    /**
     * 当前该邀请谁：受众条件 ∩ 调用者数据范围，只取在用（{@code active}）的联系人，按联系人 id 升序。
     * 没设受众就是空名单。取到第 {@link #MAX_PARTICIPANTS}+1 个人就直接拒绝，绝不悄悄截断成半批邀请。
     */
    public List<ContactView> recipients(TenantContext ctx, UUID surveyId) {
        requireSurvey(ctx, surveyId);
        return tenantScope.call(ctx.tenantId(), () -> {
            access.scopeOf(ctx);
            return audiences.find(surveyId)
                    .map(audience -> collect(ctx, audience))
                    .orElseGet(List::of);
        });
    }

    private List<ContactView> collect(TenantContext ctx, SurveyAudienceView audience) {
        ContactQuery query = audience.query().withLimit(PAGE);
        List<ContactView> chosen = new ArrayList<>();
        String cursor = null;
        do {
            ContactPage page = contacts.search(ctx, query.withCursor(cursor));
            for (ContactView contact : page.items()) {
                if (!contact.isActive()) {
                    continue;
                }
                // 停在上限，不是"超过一页之后才发现"：这个上限限的就是内存与定义体积的爆炸半径。
                if (chosen.size() == MAX_PARTICIPANTS) {
                    throw new InvalidContactRequestException(
                            "the audience of survey " + audience.surveyId() + " exceeds " + MAX_PARTICIPANTS
                                    + " contacts; narrow it down before publishing");
                }
                chosen.add(contact);
            }
            cursor = page.nextCursor();
        } while (cursor != null);
        return List.copyOf(chosen);
    }

    /** 问卷不存在、无权或属于别的租户，都等同"这份问卷没有受众可设"。 */
    private void requireSurvey(TenantContext ctx, UUID surveyId) {
        if (surveyId == null) {
            throw new InvalidContactRequestException("surveyId is required");
        }
        try {
            surveys.get(ctx, surveyId);
        } catch (SurveyNotFoundException e) {
            throw new ContactNotFoundException("survey not found: " + surveyId);
        } catch (SurveyAccessDeniedException e) {
            throw new ContactAccessDeniedException(e.reason(), e.getMessage());
        }
    }
}
