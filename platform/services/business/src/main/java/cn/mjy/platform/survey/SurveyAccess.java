package cn.mjy.platform.survey;

import cn.mjy.platform.access.AccessDecisionService;
import cn.mjy.platform.access.Decision;
import cn.mjy.platform.access.DecisionReason;
import cn.mjy.platform.access.Permission;
import cn.mjy.platform.shared.TenantContext;
import java.util.UUID;
import org.springframework.stereotype.Component;

/**
 * 把 access 模块的判定翻译成本模块的异常。判定本身完全交给 {@link AccessDecisionService}，这里不加任何规则：
 * 资源不存在（含别的租户的资源）→ 404；其余拒绝 → 403 并原样带上原因（包括 APPROVAL_REQUIRED）。
 */
@Component
class SurveyAccess {

    private final AccessDecisionService access;

    SurveyAccess(AccessDecisionService access) {
        this.access = access;
    }

    void require(TenantContext ctx, Permission permission, UUID resourceId) {
        Decision decision = access.can(ctx, permission, resourceId);
        if (!decision.allowed()) {
            throw denied(permission, resourceId, decision);
        }
    }

    /**
     * 要求调用方持有问卷的发布权，返回是否还须凭批准发布。
     *
     * <p>access 模块只在"在职成员 + 有含 publish 的授权"都成立之后才会给出 APPROVAL_REQUIRED
     * （见 {@link AccessDecisionService} 的判定顺序），所以这个原因本身就证明调用方持有发布权、只差审批；
     * 是否存在有效批准由本模块的审批记录决定。其余拒绝原样抛出，与 {@link #require} 一致。
     */
    boolean requirePublishRight(TenantContext ctx, UUID surveyId) {
        Decision decision = access.can(ctx, Permission.PUBLISH, surveyId);
        if (decision.allowed()) {
            return false;
        }
        if (decision.reason() == DecisionReason.APPROVAL_REQUIRED) {
            return true;
        }
        throw denied(Permission.PUBLISH, surveyId, decision);
    }

    private static RuntimeException denied(Permission permission, UUID resourceId, Decision decision) {
        if (decision.reason() == DecisionReason.UNKNOWN_RESOURCE) {
            return new SurveyNotFoundException("resource not found: " + resourceId);
        }
        return new SurveyAccessDeniedException(decision.reason(), permission.code() + " denied: " + decision.detail());
    }
}
