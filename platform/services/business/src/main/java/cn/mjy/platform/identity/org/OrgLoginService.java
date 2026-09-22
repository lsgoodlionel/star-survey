package cn.mjy.platform.identity.org;

import cn.mjy.platform.access.MemberService;
import cn.mjy.platform.access.SeatLimitExceededException;
import cn.mjy.platform.audit.AuditLogRepository;
import cn.mjy.platform.identity.ExternalIdentity;
import cn.mjy.platform.identity.IdentityBinding;
import cn.mjy.platform.identity.IdentityBindingService;
import cn.mjy.platform.shared.TenantId;
import cn.mjy.platform.shared.tenant.TenantDirectory;
import cn.mjy.platform.shared.tenant.TenantScope;
import cn.mjy.platform.tenant.api.NotFoundException;
import java.net.URI;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.security.SecureRandom;
import java.time.Clock;
import java.time.Instant;
import java.util.Base64;
import java.util.HexFormat;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Optional;
import java.util.UUID;
import java.util.function.Function;
import java.util.stream.Collectors;
import org.springframework.http.HttpStatus;
import org.springframework.stereotype.Service;

/**
 * 组织免登：发起 → 开放平台授权 → 回调在服务端兑换授权码 → 映射身份绑定 → 签发平台令牌。
 *
 * <ol>
 *   <li>发起：生成 state（32 字节随机，十六进制，满足企业微信"字母数字、≤128 字节"）与浏览器随机值；
 *       state 入库并绑定租户、连接、浏览器随机值的摘要，限时（默认 5 分钟）；随机值放进 HttpOnly Cookie。</li>
 *   <li>回调：先原子地烧掉 state，再核对连接、Cookie、有效期——重放、过期、换租户（行级安全下看不到别的租户的
 *       state）、换浏览器（登录 CSRF）一律拒绝；之后才用授权码去开放平台兑换。</li>
 *   <li>身份：外部身份 = (提供方, 组织, 组织级用户标识)，按租户绑定；未知成员按连接的策略拒绝或即时开通。</li>
 *   <li>会话：每次登录一行会话，令牌带 sid；撤销立即生效（见 {@link OrgSessionTokenValidator}）。</li>
 * </ol>
 */
@Service
public class OrgLoginService {

    static final String ACTION_LOGIN = "identity.org_login";
    static final String ACTION_LOGIN_DENIED = "identity.org_login.denied";
    static final String ACTION_LOGOUT = "identity.org_logout";
    static final String SYSTEM_ACTOR = "system:org_login";
    private static final int RANDOM_BYTES = 32;

    record Started(URI authorizeUrl, String browserNonce) {
    }

    record SignedIn(String accessToken, long expiresIn, UUID principalId, TenantId tenantId) {
    }

    private final OrgLoginProperties properties;
    private final TenantScope tenantScope;
    private final TenantDirectory tenants;
    private final OrgConnectionService connections;
    private final OrgLoginStateRepository states;
    private final OrgSessionRepository sessions;
    private final OrgBindingRepository orgBindings;
    private final IdentityBindingService bindings;
    private final MemberService members;
    private final SecretResolver secrets;
    private final PlatformTokenIssuer tokens;
    private final AuditLogRepository audit;
    private final Map<OrgProvider, OrgPlatformClient> clients;
    private final SecureRandom random = new SecureRandom();
    private final Clock clock = Clock.systemUTC();

    OrgLoginService(OrgLoginProperties properties, TenantScope tenantScope, TenantDirectory tenants,
            OrgConnectionService connections,
            OrgLoginStateRepository states, OrgSessionRepository sessions, OrgBindingRepository orgBindings,
            IdentityBindingService bindings, MemberService members, SecretResolver secrets,
            PlatformTokenIssuer tokens, AuditLogRepository audit, List<OrgPlatformClient> clients) {
        this.properties = properties;
        this.tenantScope = tenantScope;
        this.tenants = tenants;
        this.connections = connections;
        this.states = states;
        this.sessions = sessions;
        this.orgBindings = orgBindings;
        this.bindings = bindings;
        this.members = members;
        this.secrets = secrets;
        this.tokens = tokens;
        this.audit = audit;
        this.clients = clients.stream().collect(Collectors.toUnmodifiableMap(OrgPlatformClient::provider,
                Function.identity()));
    }

    /** 须在开放平台登记的回调地址；未配置平台对外地址时为空。 */
    String redirectUri(TenantId tenant, UUID connectionId) {
        if (!properties.isCallbackConfigured()) {
            return null;
        }
        String base = properties.callbackBaseUrl().replaceAll("/+$", "");
        return base + "/v1/auth/org/" + tenant + "/" + connectionId + "/callback";
    }

    Started start(TenantId tenant, UUID connectionId, boolean qr) {
        requireActiveTenant(tenant);
        OrgConnection connection = enabledConnection(tenant, connectionId);
        if (!properties.isCallbackConfigured()) {
            throw OrgLoginException.notConfigured("platform.identity.org-login.callback-base-url");
        }
        String state = HexFormat.of().formatHex(randomBytes());
        String nonce = Base64.getUrlEncoder().withoutPadding().encodeToString(randomBytes());
        Instant expiresAt = clock.instant().plus(properties.stateTtl());
        tenantScope.run(tenant, () -> {
            states.purgeStale();
            states.insert(tenant, state, connectionId, sha256(nonce), expiresAt);
        });
        URI url = client(connection).authorizeUrl(connection, redirectUri(tenant, connectionId), state, qr);
        return new Started(url, nonce);
    }

    SignedIn complete(TenantId tenant, UUID connectionId, String code, String state, String browserNonce) {
        String trace = UUID.randomUUID().toString();
        requireActiveTenant(tenant);
        requireValidState(tenant, connectionId, state, browserNonce);
        OrgConnection connection = enabledConnection(tenant, connectionId);
        String secret = secrets.resolve(tenant, connection.secretRef())
                .orElseThrow(() -> OrgLoginException.notConfigured("the connection secret"));
        if (code == null || code.isBlank()) {
            throw new OrgLoginException(HttpStatus.UNAUTHORIZED, OrgLoginException.PROVIDER_REJECTED,
                    "authorisation was not granted");
        }
        OrgPlatformClient.ProviderUser user = exchange(connection, secret, code, redirectUri(tenant, connectionId),
                trace);
        if (!connection.corpId().equals(user.corpId())) {
            deny(connection, user.userId(), "wrong_organisation", trace);
            throw new OrgLoginException(HttpStatus.FORBIDDEN, OrgLoginException.WRONG_ORGANISATION,
                    "authorised in a different organisation");
        }
        UUID principal = resolvePrincipal(connection, user.userId(), trace);
        return issue(connection, principal, trace);
    }

    /** 登出：撤销当前会话；令牌里没有会话（非免登签发）时什么都不做。 */
    void logout(TenantId tenant, String subject, String sessionId, String traceId) {
        if (sessionId == null) {
            return;
        }
        UUID principal = UUID.fromString(subject);
        tenantScope.run(tenant, () -> {
            if (sessions.revoke(UUID.fromString(sessionId), principal, OrgSessionRepository.REASON_LOGOUT) > 0) {
                audit.record(tenant, subject, ACTION_LOGOUT, "session/" + sessionId, traceId);
            }
        });
    }

    private void requireValidState(TenantId tenant, UUID connectionId, String state, String browserNonce) {
        if (state == null || !state.matches("[A-Za-z0-9]{32,128}") || browserNonce == null) {
            throw OrgLoginException.invalidState();
        }
        OrgLoginStateRepository.ConsumedState consumed = tenantScope.call(tenant, () -> states.consume(state))
                .orElseThrow(OrgLoginException::invalidState);
        boolean sameBrowser = MessageDigest.isEqual(
                consumed.browserHash().getBytes(StandardCharsets.US_ASCII),
                sha256(browserNonce).getBytes(StandardCharsets.US_ASCII));
        boolean valid = consumed.connectionId().equals(connectionId) && sameBrowser
                && consumed.expiresAt().isAfter(clock.instant());
        if (!valid) {
            throw OrgLoginException.invalidState();
        }
    }

    private OrgPlatformClient.ProviderUser exchange(OrgConnection connection, String secret, String code,
            String redirectUri, String trace) {
        try {
            return client(connection).exchange(connection, secret, code, redirectUri);
        } catch (OrgProviderException e) {
            if (e.kind() == OrgProviderException.Kind.WRONG_ORGANISATION
                    || e.kind() == OrgProviderException.Kind.NOT_A_MEMBER) {
                deny(connection, "unknown", e.kind().name().toLowerCase(Locale.ROOT), trace);
            }
            throw OrgLoginException.from(e);
        }
    }

    /** 外部身份 → 主体；按连接策略处理未知成员；撤销的绑定与非在职成员一律拒绝。 */
    private UUID resolvePrincipal(OrgConnection connection, String userId, String trace) {
        TenantId tenant = connection.tenantId();
        ExternalIdentity identity = connection.identityOf(userId);
        boolean jit = connection.unknownUserPolicy() == UnknownUserPolicy.JIT_STAFF;
        IdentityBinding binding = bindings.findByIdentity(tenant, identity)
                .or(() -> jit ? Optional.of(bindings.bind(tenant, identity, SYSTEM_ACTOR, trace).value())
                        : Optional.empty())
                .orElseThrow(() -> denied(connection, userId, "not_preauthorized", trace));
        UUID principal = binding.principalId();
        if (tenantScope.call(tenant, () -> orgBindings.isRevoked(principal))) {
            throw denied(connection, userId, "revoked", trace);
        }
        String actor = principal.toString();
        boolean active = members.activateOnSignIn(tenant, actor, trace);
        if (!active && jit) {
            try {
                active = members.provisionStaffBySystem(tenant, actor, trace);
            } catch (SeatLimitExceededException e) {
                deny(connection, userId, "seat_limit", trace);
                throw new OrgLoginException(HttpStatus.FORBIDDEN, OrgLoginException.SEAT_LIMIT,
                        "no seat is available for a new member");
            }
        }
        if (!active) {
            throw denied(connection, userId, "not_an_active_member", trace);
        }
        return principal;
    }

    private SignedIn issue(OrgConnection connection, UUID principal, String trace) {
        TenantId tenant = connection.tenantId();
        UUID sessionId = UUID.randomUUID();
        Instant now = clock.instant();
        Instant expiresAt = now.plus(properties.tokenTtl());
        tenantScope.run(tenant, () -> {
            sessions.insert(sessionId, tenant, principal, connection.id(), now, expiresAt);
            audit.record(tenant, principal.toString(), ACTION_LOGIN, "session/" + sessionId + " connection/"
                    + connection.id() + " provider=" + connection.provider().code(), trace);
        });
        String token = tokens.issue(tenant, principal, sessionId, connection.provider(), now, expiresAt);
        return new SignedIn(token, properties.tokenTtl().toSeconds(), principal, tenant);
    }

    private OrgLoginException denied(OrgConnection connection, String userId, String reason, String trace) {
        deny(connection, userId, reason, trace);
        return OrgLoginException.notAuthorized();
    }

    private void deny(OrgConnection connection, String userId, String reason, String trace) {
        tenantScope.run(connection.tenantId(), () -> audit.record(connection.tenantId(), SYSTEM_ACTOR,
                ACTION_LOGIN_DENIED, "connection/" + connection.id() + " external=" + userId + " reason=" + reason,
                trace));
    }

    /** 停用（suspended）、关闭或仍在开通中的租户一律不能免登；state 与授权码都不会被消耗。 */
    private void requireActiveTenant(TenantId tenant) {
        if (!tenants.isActive(tenant)) {
            throw OrgLoginException.tenantUnavailable();
        }
    }

    private OrgConnection enabledConnection(TenantId tenant, UUID connectionId) {
        return connections.find(tenant, connectionId).filter(OrgConnection::enabled)
                .orElseThrow(() -> new NotFoundException("organisation connection not found"));
    }

    private OrgPlatformClient client(OrgConnection connection) {
        OrgPlatformClient client = clients.get(connection.provider());
        if (client == null) {
            throw OrgLoginException.notConfigured("provider " + connection.provider().code());
        }
        return client;
    }

    private byte[] randomBytes() {
        byte[] bytes = new byte[RANDOM_BYTES];
        random.nextBytes(bytes);
        return bytes;
    }

    static String sha256(String value) {
        try {
            return HexFormat.of().formatHex(
                    MessageDigest.getInstance("SHA-256").digest(value.getBytes(StandardCharsets.UTF_8)));
        } catch (NoSuchAlgorithmException e) {
            throw new IllegalStateException("SHA-256 unavailable", e);
        }
    }
}
