package cn.mjy.platform.identity.org;

import cn.mjy.platform.identity.IdentityBinding;
import cn.mjy.platform.shared.security.CurrentTenant;
import java.util.UUID;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RestController;

/**
 * 管理员恢复被撤销（离职 / 禁用）的组织身份：{@code POST /v1/identity-bindings/{principalId}/reinstatement}。
 * 需要租户级 manage-members；按员工重新邀请（占席位），本人下次免登时生效。未撤销 409，别的租户 404。审计
 * {@code identity.binding.reinstate}。
 */
@RestController
class BindingReinstatementController {

    private final OrgDirectorySyncService sync;
    private final CurrentTenant currentTenant;

    BindingReinstatementController(OrgDirectorySyncService sync, CurrentTenant currentTenant) {
        this.sync = sync;
        this.currentTenant = currentTenant;
    }

    @PostMapping("/v1/identity-bindings/{principalId}/reinstatement")
    ReinstatedView reinstate(@PathVariable UUID principalId) {
        IdentityBinding binding = sync.reinstate(currentTenant.require(), principalId);
        return new ReinstatedView(binding.principalId().toString(), binding.identity().provider(),
                binding.identity().appId(), binding.identity().externalId());
    }

    record ReinstatedView(String principalId, String provider, String appId, String externalId) {
    }
}
