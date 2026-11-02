package cn.mjy.platform.tenant;

import cn.mjy.platform.shared.security.CurrentTenant;
import cn.mjy.platform.platformadmin.PlatformPersonnelAccess;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.util.List;
import java.util.UUID;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.security.access.AccessDeniedException;
import org.springframework.security.core.Authentication;
import org.springframework.security.core.context.SecurityContextHolder;
import org.springframework.security.oauth2.jwt.Jwt;
import org.springframework.stereotype.Component;

/**
 * 控制平面授权：默认保留既有运营角色以兼容旧安装；显式启用后要求有效人员会话与独立 grant。
 * 启用前必须配置独立探针凭据，未带令牌的请求在过滤器链上先返回 401。
 */
@Component
public class PlatformOperatorGuard {
    private final PlatformPersonnelAccess personnel;
    private final boolean enforcePersonnel;

    public PlatformOperatorGuard(PlatformPersonnelAccess personnel,
            @Value("${platform.admin.enforce-personnel:false}") boolean enforcePersonnel,
            @Value("${PLATFORM_PROBE_HMAC_SECRET:}") String probeSecret,
            @Value("${PLATFORM_PROBE_INSTALLATION_ID:}") String installation,
            @Value("${platform.security.jwt-hmac-secret:}") String personnelSecret,
            @Value("${PLATFORM_ENGINE_EVENTS_SECRET:}") String engineSecret) {
        this.personnel = personnel;
        this.enforcePersonnel = enforcePersonnel;
        if (enforcePersonnel && !probeReady(probeSecret, installation, personnelSecret, engineSecret))
            throw new IllegalStateException("personnel guard requires independent probe configuration");
    }

    /** 校验当前调用方是平台运营，返回其标识（令牌 subject），用于审计。 */
    public String requireOperator() {
        if (enforcePersonnel) return personnel.requireActor();
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

    private static boolean probeReady(String probe, String installation, String personnel, String engine) {
        byte[] key = probe.getBytes(StandardCharsets.UTF_8);
        if (key.length < 32 || MessageDigest.isEqual(key, personnel.getBytes(StandardCharsets.UTF_8))
                || MessageDigest.isEqual(key, engine.getBytes(StandardCharsets.UTF_8))) return false;
        try { return UUID.fromString(installation).toString().equals(installation); }
        catch (IllegalArgumentException rejected) { return false; }
    }
}
