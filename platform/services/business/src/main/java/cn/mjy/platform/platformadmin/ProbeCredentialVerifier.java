package cn.mjy.platform.platformadmin;

import java.time.Duration;
import java.time.Instant;
import java.util.List;
import java.util.Optional;
import java.util.Set;
import javax.crypto.spec.SecretKeySpec;
import org.springframework.security.oauth2.jose.jws.MacAlgorithm;
import org.springframework.security.oauth2.jwt.Jwt;
import org.springframework.security.oauth2.jwt.JwtDecoder;
import org.springframework.security.oauth2.jwt.JwtValidators;
import org.springframework.security.oauth2.jwt.NimbusJwtDecoder;

/** Isolated verifier only: not registered as a bean or connected to any endpoint. */
final class ProbeCredentialVerifier {
    private static final Set<String> ALLOWED = Set.of("probe.tenant.create", "probe.plan.create", "probe.onboard",
            "probe.tenant.activate", "probe.engine.register", "probe.engine.secret.read");
    private final JwtDecoder decoder;

    ProbeCredentialVerifier(byte[] independentKey) {
        if (independentKey == null || independentKey.length < 32) throw new IllegalArgumentException("probe key too short");
        var configured = NimbusJwtDecoder.withSecretKey(new SecretKeySpec(independentKey.clone(), "HmacSHA256"))
                .macAlgorithm(MacAlgorithm.HS256).build();
        configured.setJwtValidator(JwtValidators.createDefaultWithIssuer("survey-probe"));
        this.decoder = configured;
    }

    Optional<Set<String>> verify(String token) {
        try {
            Jwt jwt = decoder.decode(token);
            Instant now = Instant.now(), issued = jwt.getIssuedAt(), expires = jwt.getExpiresAt();
            List<String> scopes = jwt.getClaimAsStringList("scope");
            if (!"setup-probe".equals(jwt.getSubject()) || !jwt.getAudience().equals(List.of("survey-setup-probe"))
                    || issued == null || expires == null || issued.isAfter(now.plusSeconds(30)) || !expires.isAfter(now)
                    || !expires.isAfter(issued) || Duration.between(issued, expires).compareTo(Duration.ofMinutes(5)) > 0
                    || scopes == null || scopes.isEmpty() || !ALLOWED.containsAll(scopes)) return Optional.empty();
            return Optional.of(Set.copyOf(scopes));
        } catch (RuntimeException rejected) {
            return Optional.empty();
        }
    }
}
