package cn.mjy.platform.shared.security;

import cn.mjy.platform.shared.TenantContext;
import cn.mjy.platform.shared.TenantId;
import java.util.List;
import java.util.UUID;
import org.springframework.security.access.AccessDeniedException;
import org.springframework.security.core.Authentication;
import org.springframework.security.core.context.SecurityContextHolder;
import org.springframework.security.oauth2.jwt.Jwt;
import org.springframework.stereotype.Component;

/**
 * 从已验签的令牌构造 {@link TenantContext}。令牌缺少租户声明时拒绝（403），
 * 请求头、查询参数一律不参与租户判定。
 */
@Component
public class CurrentTenant {

    public static final String TENANT_CLAIM = "tenant_id";
    public static final String ROLES_CLAIM = "roles";

    public TenantContext require() {
        Authentication authentication = SecurityContextHolder.getContext().getAuthentication();
        if (authentication == null || !(authentication.getPrincipal() instanceof Jwt jwt)) {
            throw new AccessDeniedException("no verified identity");
        }
        String tenant = jwt.getClaimAsString(TENANT_CLAIM);
        if (tenant == null || tenant.isBlank()) {
            throw new AccessDeniedException("token carries no tenant");
        }
        List<String> roles = jwt.hasClaim(ROLES_CLAIM) ? jwt.getClaimAsStringList(ROLES_CLAIM) : List.of();
        return new TenantContext(TenantId.of(tenant), jwt.getSubject(), roles, UUID.randomUUID().toString());
    }
}
