package cn.mjy.platform.identity.org;

import cn.mjy.platform.shared.TenantId;
import cn.mjy.platform.tenant.api.NotFoundException;
import java.net.URI;
import java.util.UUID;
import org.springframework.http.CacheControl;
import org.springframework.http.HttpHeaders;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseCookie;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.CookieValue;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

/**
 * 组织免登的匿名端点（独立过滤链放行，见 {@link OrgLoginSecurityConfig}）。
 *
 * <ul>
 *   <li>{@code GET /v1/auth/org/{tenantId}/{connectionId}/start[?mode=qr][&client=admin_web]}：302 到开放平台授权页，
 *       同时种下浏览器绑定 Cookie（HttpOnly、SameSite=Lax、仅本路径）。</li>
 *   <li>{@code GET /v1/auth/org/{tenantId}/{connectionId}/callback?code&state}：开放平台回跳地址。
 *       原流程返回 JSON；admin_web 流程只保存一次性交接并跳回固定管理端，不在 URL 中放令牌或主体。</li>
 *   <li>{@code POST /v1/auth/org/{tenantId}/handoff}：绑定同一浏览器原子消费交接，再创建会话并返回令牌。</li>
 * </ul>
 * 路径里的租户只用来限定数据库作用域：state 只在发起它的租户里可见，换租户即找不到。
 */
@RestController
@RequestMapping(OrgLoginController.BASE_PATH)
public class OrgLoginController {

    static final String BASE_PATH = "/v1/auth/org";
    public static final String BROWSER_COOKIE = "mjy_org_login";
    static final String HANDOFF_COOKIE = "mjy_org_handoff";

    private final OrgLoginService login;
    private final OrgLoginHandoffService handoffs;
    private final OrgLoginProperties properties;

    OrgLoginController(OrgLoginService login, OrgLoginHandoffService handoffs, OrgLoginProperties properties) {
        this.login = login;
        this.handoffs = handoffs;
        this.properties = properties;
    }

    @GetMapping("/{tenantId}/{connectionId}/start")
    ResponseEntity<Void> start(@PathVariable String tenantId, @PathVariable String connectionId,
            @RequestParam(required = false) String mode, @RequestParam(required = false) String client) {
        boolean adminWeb = adminWebClient(client);
        if (adminWeb && !properties.isAdminWebConfigured()) {
            throw OrgLoginException.notConfigured("platform.identity.org-login.admin-web-base-url");
        }
        OrgLoginService.Started started = login.start(tenant(tenantId), connection(connectionId), "qr".equals(mode),
                adminWeb);
        return ResponseEntity.status(HttpStatus.FOUND)
                .location(started.authorizeUrl())
                .cacheControl(CacheControl.noStore())
                .header(HttpHeaders.SET_COOKIE, cookie(started.browserNonce(), properties.stateTtl().toSeconds()))
                .build();
    }

    @GetMapping("/{tenantId}/{connectionId}/callback")
    ResponseEntity<?> callback(@PathVariable String tenantId, @PathVariable String connectionId,
            @RequestParam(required = false) String code, @RequestParam(required = false) String state,
            @RequestParam(required = false) String client,
            @CookieValue(name = BROWSER_COOKIE, required = false) String browserNonce) {
        boolean adminWeb = adminWebClient(client);
        if (adminWeb != OrgLoginService.isAdminWebState(state)) {
            throw OrgLoginException.invalidState();
        }
        OrgLoginService.Authenticated authenticated = login.authenticate(tenant(tenantId), connection(connectionId),
                code, state, browserNonce);
        if (adminWeb) {
            OrgLoginHandoffService.CreatedHandoff created = handoffs.create(authenticated);
            return ResponseEntity.status(HttpStatus.FOUND)
                    .location(URI.create(properties.adminWebCallbackUrl(authenticated.tenantId(),
                            created.opaqueHandoff())))
                    .cacheControl(CacheControl.noStore())
                    .header(HttpHeaders.SET_COOKIE, cookie("", 0))
                    .header(HttpHeaders.SET_COOKIE, handoffCookie(authenticated.tenantId(), created.browserNonce(), 60))
                    .build();
        }
        OrgLoginService.SignedIn signedIn = login.issue(authenticated, UUID.randomUUID().toString());
        return ResponseEntity.ok()
                .cacheControl(CacheControl.noStore())
                .header(HttpHeaders.SET_COOKIE, cookie("", 0))
                .body(tokenView(signedIn));
    }

    @PostMapping("/{tenantId}/handoff")
    ResponseEntity<TokenView> exchange(@PathVariable String tenantId,
            @RequestBody(required = false) ExchangeHandoff body,
            @CookieValue(name = HANDOFF_COOKIE, required = false) String browserNonce) {
        String opaque = body == null ? null : body.handoff();
        OrgLoginService.SignedIn signedIn = handoffs.exchange(tenant(tenantId), opaque, browserNonce);
        return ResponseEntity.ok()
                .cacheControl(CacheControl.noStore())
                .header(HttpHeaders.SET_COOKIE, handoffCookie(tenant(tenantId), "", 0))
                .body(tokenView(signedIn));
    }

    private String cookie(String value, long maxAgeSeconds) {
        return ResponseCookie.from(BROWSER_COOKIE, value)
                .httpOnly(true)
                .secure(properties.secureCookie())
                .sameSite("Lax")
                .path(BASE_PATH + "/")
                .maxAge(maxAgeSeconds)
                .build()
                .toString();
    }

    private String handoffCookie(TenantId tenant, String value, long maxAgeSeconds) {
        return ResponseCookie.from(HANDOFF_COOKIE, value)
                .httpOnly(true)
                .secure(properties.secureCookie())
                .sameSite("Strict")
                .path(BASE_PATH + "/" + tenant + "/handoff")
                .maxAge(maxAgeSeconds)
                .build()
                .toString();
    }

    private static boolean adminWebClient(String client) {
        if (client == null || client.isBlank()) {
            return false;
        }
        if (!"admin_web".equals(client)) {
            throw OrgLoginException.invalidState();
        }
        return true;
    }

    private static TokenView tokenView(OrgLoginService.SignedIn signedIn) {
        return new TokenView(signedIn.accessToken(), "Bearer", signedIn.expiresIn(),
                signedIn.principalId().toString(), signedIn.tenantId().toString());
    }

    private static TenantId tenant(String value) {
        try {
            return TenantId.of(value);
        } catch (IllegalArgumentException e) {
            throw new NotFoundException("organisation connection not found");
        }
    }

    private static UUID connection(String value) {
        try {
            return UUID.fromString(value);
        } catch (IllegalArgumentException e) {
            throw new NotFoundException("organisation connection not found");
        }
    }

    record TokenView(String accessToken, String tokenType, long expiresIn, String principalId, String tenantId) {
    }

    record ExchangeHandoff(String handoff) {
    }
}
