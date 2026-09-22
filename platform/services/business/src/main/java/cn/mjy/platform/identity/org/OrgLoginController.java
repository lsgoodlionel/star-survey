package cn.mjy.platform.identity.org;

import cn.mjy.platform.shared.TenantId;
import cn.mjy.platform.tenant.api.NotFoundException;
import java.util.UUID;
import org.springframework.http.CacheControl;
import org.springframework.http.HttpHeaders;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseCookie;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.CookieValue;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

/**
 * 组织免登的匿名端点（独立过滤链放行，见 {@link OrgLoginSecurityConfig}）。
 *
 * <ul>
 *   <li>{@code GET /v1/auth/org/{tenantId}/{connectionId}/start[?mode=qr]}：302 到开放平台授权页，
 *       同时种下浏览器绑定 Cookie（HttpOnly、SameSite=Lax、仅本路径）。</li>
 *   <li>{@code GET /v1/auth/org/{tenantId}/{connectionId}/callback?code&state}：开放平台回跳地址。
 *       成功返回 JSON {accessToken, tokenType, expiresIn, principalId, tenantId}，不缓存，并清掉 Cookie。</li>
 * </ul>
 * 路径里的租户只用来限定数据库作用域：state 只在发起它的租户里可见，换租户即找不到。
 */
@RestController
@RequestMapping(OrgLoginController.BASE_PATH)
public class OrgLoginController {

    static final String BASE_PATH = "/v1/auth/org";
    public static final String BROWSER_COOKIE = "mjy_org_login";

    private final OrgLoginService login;
    private final OrgLoginProperties properties;

    OrgLoginController(OrgLoginService login, OrgLoginProperties properties) {
        this.login = login;
        this.properties = properties;
    }

    @GetMapping("/{tenantId}/{connectionId}/start")
    ResponseEntity<Void> start(@PathVariable String tenantId, @PathVariable String connectionId,
            @RequestParam(required = false) String mode) {
        OrgLoginService.Started started = login.start(tenant(tenantId), connection(connectionId), "qr".equals(mode));
        return ResponseEntity.status(HttpStatus.FOUND)
                .location(started.authorizeUrl())
                .cacheControl(CacheControl.noStore())
                .header(HttpHeaders.SET_COOKIE, cookie(started.browserNonce(), properties.stateTtl().toSeconds()))
                .build();
    }

    @GetMapping("/{tenantId}/{connectionId}/callback")
    ResponseEntity<TokenView> callback(@PathVariable String tenantId, @PathVariable String connectionId,
            @RequestParam(required = false) String code, @RequestParam(required = false) String state,
            @CookieValue(name = BROWSER_COOKIE, required = false) String browserNonce) {
        OrgLoginService.SignedIn signedIn = login.complete(tenant(tenantId), connection(connectionId), code, state,
                browserNonce);
        TokenView body = new TokenView(signedIn.accessToken(), "Bearer", signedIn.expiresIn(),
                signedIn.principalId().toString(), signedIn.tenantId().toString());
        return ResponseEntity.ok()
                .cacheControl(CacheControl.noStore())
                .header(HttpHeaders.SET_COOKIE, cookie("", 0))
                .body(body);
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
}
