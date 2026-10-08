package cn.mjy.platform.identity.org;

import cn.mjy.platform.shared.TenantId;
import java.net.URI;
import java.time.Duration;
import org.springframework.boot.context.properties.ConfigurationProperties;
import org.springframework.boot.context.properties.bind.DefaultValue;

/**
 * 组织免登配置（前缀 {@code platform.identity.org-login}）。开放平台地址默认指向官方正式环境，
 * 测试把它们指向本地替身；接入真实企业只需配置 callback-base-url 与各租户的密钥，不改代码。
 *
 * @param callbackBaseUrl 平台对外的 HTTPS 基础地址；回调地址 = 它 + /v1/auth/org/{租户}/{连接}/callback，
 *                        须在各开放平台登记（企业微信可信域名、钉钉/飞书重定向 URL）。为空时免登一律 503
 * @param adminWebBaseUrl 管理端固定基础地址；必须是 HTTPS，测试可用 http://127.0.0.1
 * @param stateTtl        state 有效期（开放平台授权码本身 5 分钟有效）
 * @param tokenTtl        平台令牌有效期；每次请求另有会话撤销检查
 * @param httpTimeout     调用开放平台的超时
 * @param secureCookie    浏览器绑定 Cookie 是否带 Secure（生产必须为真）
 */
@ConfigurationProperties("platform.identity.org-login")
public record OrgLoginProperties(
        @DefaultValue("") String callbackBaseUrl,
        @DefaultValue("") String adminWebBaseUrl,
        @DefaultValue("PT5M") Duration stateTtl,
        @DefaultValue("PT10M") Duration tokenTtl,
        @DefaultValue("PT10S") Duration httpTimeout,
        @DefaultValue("true") boolean secureCookie,
        @DefaultValue("https://open.weixin.qq.com/connect/oauth2/authorize") String wecomAuthorizeUrl,
        @DefaultValue("https://login.work.weixin.qq.com/wwlogin/sso/login") String wecomQrLoginUrl,
        @DefaultValue("https://qyapi.weixin.qq.com") String wecomApiBaseUrl,
        @DefaultValue("https://login.dingtalk.com/oauth2/auth") String dingtalkAuthorizeUrl,
        @DefaultValue("https://api.dingtalk.com") String dingtalkApiBaseUrl,
        @DefaultValue("https://oapi.dingtalk.com") String dingtalkOapiBaseUrl,
        @DefaultValue("https://accounts.feishu.cn/open-apis/authen/v1/authorize") String feishuAuthorizeUrl,
        @DefaultValue("https://open.feishu.cn") String feishuApiBaseUrl) {

    public OrgLoginProperties {
        requirePositive(stateTtl, "state-ttl");
        requirePositive(tokenTtl, "token-ttl");
        requirePositive(httpTimeout, "http-timeout");
        validateAdminWebBaseUrl(adminWebBaseUrl);
        validateSameOrigin(callbackBaseUrl, adminWebBaseUrl);
    }

    boolean isCallbackConfigured() {
        return callbackBaseUrl != null && !callbackBaseUrl.isBlank();
    }

    boolean isAdminWebConfigured() {
        return adminWebBaseUrl != null && !adminWebBaseUrl.isBlank();
    }

    String adminWebCallbackUrl(TenantId tenant, String handoff) {
        if (!isAdminWebConfigured()) {
            throw OrgLoginException.notConfigured("platform.identity.org-login.admin-web-base-url");
        }
        return adminWebBaseUrl.replaceAll("/+$", "") + "/auth/callback?tenant=" + tenant + "&handoff=" + handoff;
    }

    private static void validateAdminWebBaseUrl(String value) {
        if (value == null || value.isBlank()) {
            return;
        }
        URI uri;
        try {
            uri = URI.create(value);
        } catch (IllegalArgumentException e) {
            throw new IllegalArgumentException("platform.identity.org-login.admin-web-base-url must be a URL", e);
        }
        boolean https = "https".equalsIgnoreCase(uri.getScheme());
        boolean loopbackTest = "http".equalsIgnoreCase(uri.getScheme()) && "127.0.0.1".equals(uri.getHost());
        if ((!https && !loopbackTest) || uri.getHost() == null || uri.getUserInfo() != null
                || uri.getQuery() != null || uri.getFragment() != null) {
            throw new IllegalArgumentException(
                    "platform.identity.org-login.admin-web-base-url must be HTTPS (or http://127.0.0.1 for tests)");
        }
    }

    private static void validateSameOrigin(String callbackBaseUrl, String adminWebBaseUrl) {
        if (callbackBaseUrl == null || callbackBaseUrl.isBlank()
                || adminWebBaseUrl == null || adminWebBaseUrl.isBlank()) {
            return;
        }
        URI callback;
        URI admin;
        try {
            callback = URI.create(callbackBaseUrl);
            admin = URI.create(adminWebBaseUrl);
        } catch (IllegalArgumentException e) {
            throw new IllegalArgumentException(
                    "platform.identity.org-login callback and admin web base URLs must have the same origin", e);
        }
        if (!same(callback.getScheme(), admin.getScheme())
                || !same(callback.getHost(), admin.getHost())
                || effectivePort(callback) != effectivePort(admin)) {
            throw new IllegalArgumentException(
                    "platform.identity.org-login callback and admin web base URLs must have the same origin");
        }
    }

    private static boolean same(String left, String right) {
        return left != null && right != null && left.equalsIgnoreCase(right);
    }

    private static int effectivePort(URI uri) {
        if (uri.getPort() >= 0) {
            return uri.getPort();
        }
        return "https".equalsIgnoreCase(uri.getScheme()) ? 443
                : "http".equalsIgnoreCase(uri.getScheme()) ? 80 : -1;
    }

    private static void requirePositive(Duration value, String name) {
        if (value == null || value.isZero() || value.isNegative()) {
            throw new IllegalArgumentException("platform.identity.org-login." + name + " must be positive");
        }
    }
}
