package cn.mjy.platform.delivery;

import cn.mjy.platform.access.AccessDecisionService;
import cn.mjy.platform.access.Decision;
import cn.mjy.platform.access.DecisionReason;
import cn.mjy.platform.access.Permission;
import cn.mjy.platform.shared.TenantContext;
import java.util.UUID;
import org.springframework.stereotype.Component;

/**
 * 把 access 模块的判定翻译成本模块的异常，本身不加任何规则（与 survey 模块的 SurveyAccess 同构）。
 *
 * <p>权限选择：投放是对问卷这份资源的写操作——建链接、发邀请、设催答规则都会改变"谁能拿到这份问卷"，
 * 因此一律要求问卷范围上的 {@link Permission#EDIT}；只读接口要求 {@link Permission#VIEW}。
 * 不新增权限码：权限目录属于 access 车道的迁移号段（V400），本车道不改它。
 *
 * <p>资源不存在或属于别的租户 → 404（两者不可区分）；其余拒绝 → 403 并原样带上原因。
 */
@Component
class DeliveryAccess {

    /** 建链接、发任务、设规则。 */
    static final Permission SEND = Permission.EDIT;
    /** 查链接、查任务进度。 */
    static final Permission READ = Permission.VIEW;

    private final AccessDecisionService access;

    DeliveryAccess(AccessDecisionService access) {
        this.access = access;
    }

    void require(TenantContext ctx, Permission permission, UUID surveyId) {
        Decision decision = access.can(ctx, permission, surveyId);
        if (decision.allowed()) {
            return;
        }
        if (decision.reason() == DecisionReason.UNKNOWN_RESOURCE) {
            throw DeliveryExceptions.notFound("survey not found: " + surveyId);
        }
        throw DeliveryExceptions.denied(decision.reason(),
                permission.code() + " denied: " + decision.detail());
    }

    /** 新答卷通知的收件人在发送前要重新判一次查看权：成员被移除或授权到期后不再收到通知（R05-09）。 */
    boolean canStillSee(TenantContext recipient, UUID surveyId) {
        return access.can(recipient, Permission.VIEW_STATISTICS, surveyId).allowed()
                || access.can(recipient, Permission.VIEW_RAW_RESPONSES, surveyId).allowed();
    }
}
