package cn.mjy.platform.identity.org;

import cn.mjy.platform.shared.TenantContext;
import cn.mjy.platform.shared.security.CurrentTenant;
import org.springframework.http.ResponseEntity;
import org.springframework.security.core.annotation.AuthenticationPrincipal;
import org.springframework.security.oauth2.jwt.Jwt;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RestController;

/** 登出：撤销当前令牌对应的免登会话，令牌随即失效（走主 JWT 过滤链）。 */
@RestController
public class OrgSessionController {

    private final OrgLoginService login;
    private final CurrentTenant currentTenant;

    OrgSessionController(OrgLoginService login, CurrentTenant currentTenant) {
        this.login = login;
        this.currentTenant = currentTenant;
    }

    @PostMapping("/v1/auth/logout")
    ResponseEntity<Void> logout(@AuthenticationPrincipal Jwt jwt) {
        TenantContext ctx = currentTenant.require();
        login.logout(ctx.tenantId(), ctx.actorId(), jwt.getClaimAsString(PlatformTokenIssuer.SESSION_CLAIM),
                ctx.traceId());
        return ResponseEntity.noContent().build();
    }
}
