package cn.mjy.platform.platformadmin;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.when;

import java.time.Instant;
import java.util.List;
import java.util.UUID;
import org.junit.jupiter.api.Test;
import org.springframework.security.oauth2.jwt.Jwt;
import org.springframework.security.oauth2.server.resource.authentication.JwtAuthenticationToken;

class PlatformAdminAuthorizationTest {
    private final PlatformAdminGrantLookup grants = mock(PlatformAdminGrantLookup.class);
    private final PlatformAdminAuthorization authorization = new PlatformAdminAuthorization(grants);
    private final UUID tenant = UUID.randomUUID();
    private final UUID subject = UUID.randomUUID();
    private JwtAuthenticationToken identity(String issuer, boolean withSession) {
        var builder = Jwt.withTokenValue("test").header("alg", "HS256")
                .issuer(issuer).subject(subject.toString()).claim("tenant_id", tenant.toString())
                .claim("roles", List.of("tenant_owner"))
                .issuedAt(Instant.now()).expiresAt(Instant.now().plusSeconds(300));
        if (withSession) builder.claim("sid", UUID.randomUUID().toString());
        return new JwtAuthenticationToken(builder.build(), List.of());
    }
    @Test void onlyIndependentGrantForVerifiedOrgIdentityAllowsAccess() {
        when(grants.isActive(tenant, subject)).thenReturn(true);
        assertThat(authorization.isAdmin(identity("mjy-platform", true))).isTrue();
        assertThat(authorization.isAdmin(identity("other-issuer", true))).isFalse();
        assertThat(authorization.isAdmin(identity("mjy-platform", false))).isFalse();
        assertThat(authorization.isAdmin(null)).isFalse();
    }
    @Test void tenantOwnerAndMachineClaimsDoNotCreatePlatformGrant() {
        var forged = Jwt.withTokenValue("probe").header("alg", "HS256").issuer("mjy-platform")
                .subject(subject.toString()).claim("tenant_id", tenant.toString())
                .claim("roles", List.of("platform_operator", "tenant_owner"))
                .issuedAt(Instant.now()).expiresAt(Instant.now().plusSeconds(300)).build();
        assertThat(authorization.isAdmin(new JwtAuthenticationToken(forged, List.of()))).isFalse();
        assertThat(authorization.isAdmin(new JwtAuthenticationToken(identity("mjy-platform", true).getToken()))).isFalse();
        assertThat(authorization.isAdmin(identity("mjy-platform", true))).isFalse();
    }
    @Test void grantRevocationAndLookupFailureFailClosed() {
        when(grants.isActive(tenant, subject)).thenReturn(true, false).thenThrow(new IllegalStateException("unavailable"));
        var auth = identity("mjy-platform", true);
        assertThat(authorization.isAdmin(auth)).isTrue();
        assertThat(authorization.isAdmin(auth)).isFalse();
        assertThat(authorization.isAdmin(auth)).isFalse();
    }
}
