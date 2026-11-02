package cn.mjy.platform.platformadmin;

import static org.assertj.core.api.Assertions.assertThat;
import com.nimbusds.jose.jwk.source.ImmutableSecret;
import java.nio.charset.StandardCharsets;
import java.time.Instant;
import java.util.List;
import java.util.UUID;
import javax.crypto.spec.SecretKeySpec;
import org.junit.jupiter.api.Test;
import org.springframework.security.oauth2.jose.jws.MacAlgorithm;
import org.springframework.security.oauth2.jwt.JwsHeader;
import org.springframework.security.oauth2.jwt.JwtClaimsSet;
import org.springframework.security.oauth2.jwt.JwtEncoderParameters;
import org.springframework.security.oauth2.jwt.NimbusJwtEncoder;

class ProbeCredentialVerifierTest {
    private static final String KEY = "isolated-probe-key-for-tests-only-000000000000";
    private static final String OTHER_KEY = "isolated-other-key-for-tests-only-000000000000";
    private static final UUID INSTALLATION = UUID.randomUUID(), RUN = UUID.randomUUID(), TENANT = UUID.randomUUID();

    @Test void dedicatedSignatureIssuerAudienceAndBoundedScopesAreRequired() {
        var verifier = new ProbeCredentialVerifier(KEY.getBytes(StandardCharsets.UTF_8));
        assertThat(verifier.verify(token(KEY, "survey-probe", "survey-setup-probe", List.of("probe.tenant.create"), 300)))
                .hasValueSatisfying(credential -> {
                    assertThat(credential.scopes()).containsExactly("probe.tenant.create");
                    assertThat(credential.installation()).isEqualTo(INSTALLATION);
                    assertThat(credential.run()).isEqualTo(RUN);
                    assertThat(credential.tenant()).isEqualTo(TENANT);
                    assertThat(credential.engine()).isEqualTo("production-engine-01");
                });
        assertThat(verifier.verify(token(OTHER_KEY, "survey-probe", "survey-setup-probe", List.of("probe.tenant.create"), 300))).isEmpty();
        assertThat(verifier.verify(token(KEY, "wrong", "survey-setup-probe", List.of("probe.tenant.create"), 300))).isEmpty();
        assertThat(verifier.verify(token(KEY, "survey-probe", "other-audience", List.of("probe.tenant.create"), 300))).isEmpty();
        assertThat(verifier.verify(token(KEY, "survey-probe", "survey-setup-probe", List.of("platform_operator"), 300))).isEmpty();
        assertThat(verifier.verify(token(KEY, "survey-probe", "survey-setup-probe", List.of("probe.tenant.create"), 3600))).isEmpty();
    }
    private static String token(String key, String issuer, String audience, List<String> scopes, int seconds) {
        Instant now = Instant.now();
        var claims = JwtClaimsSet.builder().issuer(issuer).subject("setup-probe").audience(List.of(audience))
                .issuedAt(now).expiresAt(now.plusSeconds(seconds)).claim("scope", scopes)
                .claim("installation_id", INSTALLATION.toString()).claim("run_id", RUN.toString()).claim("tenant_id", TENANT.toString())
                .claim("engine_id", "production-engine-01").build();
        var encoder = new NimbusJwtEncoder(new ImmutableSecret<>(new SecretKeySpec(key.getBytes(StandardCharsets.UTF_8), "HmacSHA256")));
        return encoder.encode(JwtEncoderParameters.from(JwsHeader.with(MacAlgorithm.HS256).build(), claims)).getTokenValue();
    }
}
