package cn.mjy.platform.identity.org;

import cn.mjy.platform.shared.TenantId;
import cn.mjy.platform.shared.security.CurrentTenant;
import com.nimbusds.jose.jwk.source.ImmutableSecret;
import java.nio.charset.StandardCharsets;
import java.time.Instant;
import java.util.List;
import java.util.UUID;
import javax.crypto.spec.SecretKeySpec;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.security.oauth2.jose.jws.MacAlgorithm;
import org.springframework.security.oauth2.jwt.JwsHeader;
import org.springframework.security.oauth2.jwt.JwtClaimsSet;
import org.springframework.security.oauth2.jwt.JwtEncoderParameters;
import org.springframework.security.oauth2.jwt.NimbusJwtEncoder;
import org.springframework.stereotype.Component;

/**
 * 平台令牌签发（ADR 0010：P1 用 HMAC 共享密钥；这是平台第一个签发方，与验签用同一密钥）。
 * 令牌只带主体、租户与会话标识；角色声明为空——权限一律由 access 模块按成员与授权判定，不信令牌里的角色。
 * 接入外部身份服务后改为由它签发，本类随之退役（见 ADR 0014）。
 */
@Component
class PlatformTokenIssuer {

    static final String ISSUER = "mjy-platform";
    static final String SESSION_CLAIM = "sid";
    static final String METHOD_CLAIM = "amr";

    private final NimbusJwtEncoder encoder;

    PlatformTokenIssuer(@Value("${platform.security.jwt-hmac-secret:}") String secret) {
        byte[] key = secret.getBytes(StandardCharsets.UTF_8);
        this.encoder = new NimbusJwtEncoder(new ImmutableSecret<>(new SecretKeySpec(key, "HmacSHA256")));
    }

    String issue(TenantId tenant, UUID principalId, UUID sessionId, OrgProvider provider, Instant issuedAt,
            Instant expiresAt) {
        JwtClaimsSet claims = JwtClaimsSet.builder()
                .issuer(ISSUER)
                .subject(principalId.toString())
                .id(sessionId.toString())
                .issuedAt(issuedAt)
                .notBefore(issuedAt)
                .expiresAt(expiresAt)
                .claim(CurrentTenant.TENANT_CLAIM, tenant.toString())
                .claim(CurrentTenant.ROLES_CLAIM, List.of())
                .claim(SESSION_CLAIM, sessionId.toString())
                .claim(METHOD_CLAIM, List.of("org:" + provider.code()))
                .build();
        JwsHeader header = JwsHeader.with(MacAlgorithm.HS256).type("JWT").build();
        return encoder.encode(JwtEncoderParameters.from(header, claims)).getTokenValue();
    }
}
