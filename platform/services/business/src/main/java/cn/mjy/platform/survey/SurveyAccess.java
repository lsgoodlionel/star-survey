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
        if (decision.allowed()) {
            return;
        }
        if (decision.reason() == DecisionReason.UNKNOWN_RESOURCE) {
            throw new SurveyNotFoundException("resource not found: " + resourceId);
        }
        throw new SurveyAccessDeniedException(decision.reason(),
                permission.code() + " denied: " + decision.detail());
    }
}
