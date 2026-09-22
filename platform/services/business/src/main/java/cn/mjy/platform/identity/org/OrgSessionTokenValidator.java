package cn.mjy.platform.identity.org;

import cn.mjy.platform.shared.TenantId;
import cn.mjy.platform.shared.security.CurrentTenant;
import cn.mjy.platform.shared.tenant.TenantScope;
import java.util.UUID;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.dao.DataAccessException;
import org.springframework.security.oauth2.core.OAuth2Error;
import org.springframework.security.oauth2.core.OAuth2ErrorCodes;
import org.springframework.security.oauth2.core.OAuth2TokenValidator;
import org.springframework.security.oauth2.core.OAuth2TokenValidatorResult;
import org.springframework.security.oauth2.jwt.Jwt;
import org.springframework.stereotype.Component;

/**
 * 撤销检查（离职后立即失权、登出）：带会话声明 {@code sid} 的令牌，每个请求都在其租户内核对会话仍有效、
 * 身份绑定未撤销。令牌本身短时有效（默认 10 分钟），这里保证撤销在下一个请求就生效，而不是等到过期。
 *
 * <p>会话行受行级安全保护，因此把租户声明改成别的租户（即使能重签）也查不到会话，同样拒绝。
 * 核对出错时失败即关闭。不带 {@code sid} 的令牌（外部签发方）不在此检查范围内。
 */
@Component
class OrgSessionTokenValidator implements OAuth2TokenValidator<Jwt> {

    private static final Logger log = LoggerFactory.getLogger(OrgSessionTokenValidator.class);
    private static final OAuth2TokenValidatorResult REVOKED = OAuth2TokenValidatorResult.failure(
            new OAuth2Error(OAuth2ErrorCodes.INVALID_TOKEN, "session is revoked or unknown", null));

    private final TenantScope tenantScope;
    private final OrgSessionRepository sessions;

    OrgSessionTokenValidator(TenantScope tenantScope, OrgSessionRepository sessions) {
        this.tenantScope = tenantScope;
        this.sessions = sessions;
    }

    @Override
    public OAuth2TokenValidatorResult validate(Jwt jwt) {
        String sid = jwt.getClaimAsString(PlatformTokenIssuer.SESSION_CLAIM);
        if (sid == null) {
            return OAuth2TokenValidatorResult.success();
        }
        String tenantClaim = jwt.getClaimAsString(CurrentTenant.TENANT_CLAIM);
        String subject = jwt.getSubject();
        if (tenantClaim == null || subject == null) {
            return REVOKED;
        }
        try {
            TenantId tenant = TenantId.of(tenantClaim);
            UUID session = UUID.fromString(sid);
            UUID principal = UUID.fromString(subject);
            boolean live = tenantScope.call(tenant, () -> sessions.isLive(session, principal));
            return live ? OAuth2TokenValidatorResult.success() : REVOKED;
        } catch (IllegalArgumentException e) {
            return REVOKED;
        } catch (DataAccessException e) {
            log.error("session revocation check failed; rejecting token: {}", e.getClass().getSimpleName());
            return REVOKED;
        }
    }
}
