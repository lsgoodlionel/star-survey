package cn.mjy.platform.identity.org;

import java.time.Duration;
import org.springframework.boot.context.properties.ConfigurationProperties;
import org.springframework.boot.context.properties.bind.DefaultValue;

/**
 * 组织免登配置（前缀 {@code platform.identity.org-login}）。开放平台地址默认指向官方正式环境，
 * 测试把它们指向本地替身；接入真实企业只需配置 callback-base-url 与各租户的密钥，不改代码。
 *
 * @param callbackBaseUrl 平台对外的 HTTPS 基础地址；回调地址 = 它 + /v1/auth/org/{租户}/{连接}/callback，
 *                        须在各开放平台登记（企业微信可信域名、钉钉/飞书重定向 URL）。为空时免登一律 503
 * @param stateTtl        state 有效期（开放平台授权码本身 5 分钟有效）
 * @param tokenTtl        平台令牌有效期；每次请求另有会话撤销检查
 * @param httpTimeout     调用开放平台的超时
 * @param secureCookie    浏览器绑定 Cookie 是否带 Secure（生产必须为真）
 */
@ConfigurationProperties("platform.identity.org-login")
public record OrgLoginProperties(
        @DefaultValue("") String callbackBaseUrl,
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
    }

    boolean isCallbackConfigured() {
        return callbackBaseUrl != null && !callbackBaseUrl.isBlank();
    }

    private static void requirePositive(Duration value, String name) {
        if (value == null || value.isZero() || value.isNegative()) {
            throw new IllegalArgumentException("platform.identity.org-login." + name + " must be positive");
        }
    }
}
