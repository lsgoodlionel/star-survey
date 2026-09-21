package cn.mjy.platform.tenant;

import cn.mjy.platform.shared.security.CurrentTenant;
import java.util.List;
import org.springframework.security.access.AccessDeniedException;
import org.springframework.security.core.Authentication;
import org.springframework.security.core.context.SecurityContextHolder;
import org.springframework.security.oauth2.jwt.Jwt;
import org.springframework.stereotype.Component;

/**
 * 控制平面授权：只认已验签令牌里的角色声明。缺少平台运营角色一律 403；
 * 未带令牌的请求在过滤器链上就已经是 401，到不了这里。
 */
@Component
public class PlatformOperatorGuard {

    /** 校验当前调用方是平台运营，返回其标识（令牌 subject），用于审计。 */
    public String requireOperator() {
        Authentication authentication = SecurityContextHolder.getContext().getAuthentication();
        if (authentication == null || !(authentication.getPrincipal() instanceof Jwt jwt)) {
            throw new AccessDeniedException("no verified identity");
        }
        List<String> roles = jwt.hasClaim(CurrentTenant.ROLES_CLAIM)
                ? jwt.getClaimAsStringList(CurrentTenant.ROLES_CLAIM)
                : List.of();
        if (roles == null || !roles.contains(PlatformRoles.OPERATOR)) {
            throw new AccessDeniedException("platform operator role required");
        }
        return jwt.getSubject();
    }
}
