package cn.mjy.platform.support;

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

/** 测试用令牌签发：与被测应用使用同一 HMAC 密钥。 */
@Component
public class TestTokens {

    private final NimbusJwtEncoder encoder;

    public TestTokens(@Value("${platform.security.jwt-hmac-secret}") String secret) {
        byte[] key = secret.getBytes(StandardCharsets.UTF_8);
        this.encoder = new NimbusJwtEncoder(new ImmutableSecret<>(new SecretKeySpec(key, "HmacSHA256")));
    }

    public String issue(String subject, UUID tenant, List<String> roles) {
        return encode(claims(subject).claim("tenant_id", tenant.toString()).claim("roles", roles).build());
    }

    public String issueWithoutTenant(String subject) {
        return encode(claims(subject).build());
    }

    private JwtClaimsSet.Builder claims(String subject) {
        Instant now = Instant.now();
        return JwtClaimsSet.builder().subject(subject).issuedAt(now).expiresAt(now.plusSeconds(300));
    }

    private String encode(JwtClaimsSet claims) {
        JwsHeader header = JwsHeader.with(MacAlgorithm.HS256).build();
        return encoder.encode(JwtEncoderParameters.from(header, claims)).getTokenValue();
    }
}
