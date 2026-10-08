package cn.mjy.platform.identity.org;

import cn.mjy.platform.shared.TenantId;
import cn.mjy.platform.shared.tenant.TenantScope;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.SecureRandom;
import java.time.Clock;
import java.time.Duration;
import java.time.Instant;
import java.util.Base64;
import org.springframework.stereotype.Service;

@Service
class OrgLoginHandoffService {

    private static final int RANDOM_BYTES = 32;
    private static final Duration TTL = Duration.ofSeconds(60);

    record CreatedHandoff(String opaqueHandoff, String browserNonce, Instant expiresAt) {
    }

    private final OrgLoginHandoffRepository handoffs;
    private final OrgLoginService login;
    private final TenantScope tenantScope;
    private final SecureRandom random = new SecureRandom();
    private final Clock clock = Clock.systemUTC();

    OrgLoginHandoffService(OrgLoginHandoffRepository handoffs, OrgLoginService login, TenantScope tenantScope) {
        this.handoffs = handoffs;
        this.login = login;
        this.tenantScope = tenantScope;
    }

    CreatedHandoff create(OrgLoginService.Authenticated authenticated) {
        String opaque = randomValue();
        String browser = randomValue();
        Instant now = clock.instant();
        Instant expiresAt = now.plus(TTL);
        tenantScope.run(authenticated.tenantId(), () -> {
            handoffs.purgeStale(authenticated.tenantId(), now);
            handoffs.insert(authenticated.tenantId(), OrgLoginService.sha256(opaque),
                    OrgLoginService.sha256(browser), authenticated.principalId(), authenticated.connectionId(),
                    authenticated.provider(), expiresAt);
        });
        return new CreatedHandoff(opaque, browser, expiresAt);
    }

    OrgLoginService.SignedIn exchange(TenantId tenant, String opaqueHandoff, String browserNonce) {
        if (!isOpaque(opaqueHandoff) || !isOpaque(browserNonce)) {
            throw OrgLoginException.invalidState();
        }
        Instant now = clock.instant();
        OrgLoginHandoffRepository.PendingHandoff pending = tenantScope.call(tenant,
                () -> handoffs.consume(tenant, OrgLoginService.sha256(opaqueHandoff), now))
                .orElseThrow(OrgLoginException::invalidState);
        boolean sameBrowser = MessageDigest.isEqual(
                pending.browserHash().getBytes(StandardCharsets.US_ASCII),
                OrgLoginService.sha256(browserNonce).getBytes(StandardCharsets.US_ASCII));
        if (!sameBrowser) {
            throw OrgLoginException.invalidState();
        }
        return login.issue(new OrgLoginService.Authenticated(tenant, pending.principalId(), pending.connectionId(),
                pending.provider()), java.util.UUID.randomUUID().toString());
    }

    private String randomValue() {
        byte[] bytes = new byte[RANDOM_BYTES];
        random.nextBytes(bytes);
        return Base64.getUrlEncoder().withoutPadding().encodeToString(bytes);
    }

    private static boolean isOpaque(String value) {
        return value != null && value.matches("[A-Za-z0-9_-]{43}");
    }
}
