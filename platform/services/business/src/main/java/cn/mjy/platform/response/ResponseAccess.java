package cn.mjy.platform.response;

import cn.mjy.platform.access.AccessDecisionService;
import cn.mjy.platform.access.Decision;
import cn.mjy.platform.access.DecisionReason;
import cn.mjy.platform.access.Permission;
import cn.mjy.platform.shared.TenantContext;
import java.util.UUID;
import org.springframework.stereotype.Component;

/**
 * 把 access 模块的判定翻译成本模块的异常，不加任何自己的规则：
 * 资源不存在（含别的租户的资源）→ 404；其余拒绝 → 403 并带上原因。
 */
@Component
class ResponseAccess {

    private final AccessDecisionService access;

    ResponseAccess(AccessDecisionService access) {
        this.access = access;
    }

    void require(TenantContext ctx, Permission permission, UUID surveyId) {
        Decision decision = access.can(ctx, permission, surveyId);
        if (decision.allowed()) {
            return;
        }
        if (decision.reason() == DecisionReason.UNKNOWN_RESOURCE) {
            throw new ResponseNotFoundException("survey not found: " + surveyId);
        }
        throw new ResponseAccessDeniedException(decision.reason(), permission.code() + " denied: " + decision.detail());
    }
}
