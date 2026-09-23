package cn.mjy.platform.survey.template;

import cn.mjy.platform.access.AccessDecisionService;
import cn.mjy.platform.access.Decision;
import cn.mjy.platform.access.DecisionReason;
import cn.mjy.platform.access.Permission;
import cn.mjy.platform.shared.TenantContext;
import java.util.UUID;
import org.springframework.stereotype.Component;

/**
 * 把 access 模块的判定翻译成本模块的异常，规则一条不加（与 {@code SurveyAccess} 同构）：
 * 资源不存在或属于别的租户 → 404；其余拒绝 → 403 并带上原因。
 *
 * <p>模板**没有**进资源树。理由：资源树的节点是"能被授权、能被移动、能装东西"的东西
 * （项目 / 文件夹 / 问卷），加一种节点要同时改枚举、改 V401 的 CHECK、改 V402 的触发器，
 * 而模板既不需要层级也不需要逐个授权。模板用的是既有权限词汇：
 *
 * <ul>
 *   <li>从问卷建模板 / 加版本 → 那份**问卷**上的 {@link Permission#EDIT}（资源树照常生效）；
 *   <li>审核（批准 / 驳回 / 下架）→ 租户级 {@link Permission#APPROVE_PUBLISH}，
 *       也就是既有的"发布审核员"角色；不新增权限码，免得动 V400 的权限表与角色矩阵；
 *   <li>浏览模板库、复制 → 租户级 {@link Permission#VIEW}；复制出来的问卷还要过
 *       {@code SurveyService.create} 对目标父节点的 EDIT 判定。
 * </ul>
 */
@Component
class TemplateAccess {

    private final AccessDecisionService access;

    TemplateAccess(AccessDecisionService access) {
        this.access = access;
    }

    /** 那份问卷上的权限（资源树判定，别的租户的问卷 → 404）。 */
    void requireOnSurvey(TenantContext ctx, Permission permission, UUID surveyId) {
        Decision decision = access.can(ctx, permission, surveyId);
        if (!decision.allowed()) {
            throw denied(permission, surveyId, decision);
        }
    }

    /** 租户级权限（没有具体资源）。 */
    void requireInTenant(TenantContext ctx, Permission permission) {
        Decision decision = access.canInTenant(ctx, permission);
        if (!decision.allowed()) {
            throw new TemplateAccessDeniedException(
                    decision.reason(), permission.code() + " denied: " + decision.detail());
        }
    }

    /**
     * 只要求"本租户的在职成员"。模板库是**全租户共用的参考资料**，不挂在资源树上，
     * 用租户级 {@code view} 当门槛会把只在某个项目上被授权的成员挡在外面
     * （编辑者通常正是这样授权的），而他们恰恰是要用模板的人。
     *
     * <p>做法是借 {@code AccessDecisionService} 的判定顺序：它先查资源、再查在职成员、最后查授权，
     * 所以租户级判定给出 {@code NO_MATCHING_GRANT} 就已经证明"是在职成员、只是没有租户级授权"。
     * 真正要挡的 {@code NOT_AN_ACTIVE_MEMBER} 照样抛 403。
     *
     * <p>这只放开**浏览**。复制出来的问卷仍然要过目标父节点上的编辑权
     * （由 {@code SurveyService.create} 判定），审核仍然要租户级的发布审核权。
     */
    void requireMember(TenantContext ctx) {
        Decision decision = access.canInTenant(ctx, Permission.VIEW);
        if (decision.allowed() || decision.reason() == DecisionReason.NO_MATCHING_GRANT) {
            return;
        }
        throw new TemplateAccessDeniedException(
                decision.reason(), "template library denied: " + decision.detail());
    }

    private static RuntimeException denied(Permission permission, UUID resourceId, Decision decision) {
        if (decision.reason() == DecisionReason.UNKNOWN_RESOURCE) {
            return new TemplateNotFoundException("resource not found: " + resourceId);
        }
        return new TemplateAccessDeniedException(
                decision.reason(), permission.code() + " denied: " + decision.detail());
    }
}
