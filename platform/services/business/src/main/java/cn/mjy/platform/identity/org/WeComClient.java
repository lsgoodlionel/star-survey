package cn.mjy.platform.identity.org;

import cn.mjy.platform.identity.org.OrgProviderException.Kind;
import java.net.URI;
import java.time.Duration;
import java.util.LinkedHashMap;
import java.util.Map;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Component;

/**
 * 企业微信（自建应用）。文档：
 * <ul>
 *   <li>网页授权链接 https://developer.work.weixin.qq.com/document/path/91022</li>
 *   <li>扫码登录（企业微信 Web 登录组件）https://developer.work.weixin.qq.com/document/path/98152</li>
 *   <li>获取访问用户身份 auth/getuserinfo https://developer.work.weixin.qq.com/document/path/91023</li>
 *   <li>获取 access_token https://developer.work.weixin.qq.com/document/path/91039</li>
 *   <li>读取成员 user/get https://developer.work.weixin.qq.com/document/path/90196</li>
 * </ul>
 * 授权码只能用本企业应用的 access_token 兑换，因此别的企业的码直接得到 40029。
 * 所有接口 HTTP 200 + errcode；errcode 非 0 即失败。
 */
@Component
class WeComClient implements OrgPlatformClient {

    private static final Logger log = LoggerFactory.getLogger(WeComClient.class);

    static final int INVALID_CODE = 40029;
    static final int USER_NOT_FOUND = 60111;
    private static final java.util.Set<Integer> BAD_CREDENTIALS = java.util.Set.of(40001, 40013, 40091, 40056);
    private static final java.util.Set<Integer> TOKEN_EXPIRED = java.util.Set.of(40014, 42001);
    private static final int STATUS_ACTIVE = 1;
    private static final int STATUS_DISABLED = 2;
    private static final int STATUS_NOT_ACTIVATED = 4;
    private static final int STATUS_LEFT = 5;

    private final ProviderHttp http;
    private final AppTokenCache tokens;
    private final OrgLoginProperties properties;

    WeComClient(ProviderHttp http, AppTokenCache tokens, OrgLoginProperties properties) {
        this.http = http;
        this.tokens = tokens;
        this.properties = properties;
    }

    @Override
    public OrgProvider provider() {
        return OrgProvider.WECOM;
    }

    @Override
    public URI authorizeUrl(OrgConnection connection, String redirectUri, String state, boolean qr) {
        Map<String, String> query = new LinkedHashMap<>();
        if (qr) {
            query.put("login_type", "CorpApp");
            query.put("appid", connection.corpId());
            query.put("agentid", connection.appId());
            query.put("redirect_uri", redirectUri);
            query.put("state", state);
            return URI.create(properties.wecomQrLoginUrl() + "?" + ProviderHttp.queryString(query));
        }
        query.put("appid", connection.corpId());
        query.put("redirect_uri", redirectUri);
        query.put("response_type", "code");
        query.put("scope", "snsapi_base");
        query.put("state", state);
        query.put("agentid", connection.appId());
        return URI.create(properties.wecomAuthorizeUrl() + "?" + ProviderHttp.queryString(query) + "#wechat_redirect");
    }

    @Override
    public ProviderUser exchange(OrgConnection connection, String secret, String code, String redirectUri) {
        String token = accessToken(connection, secret);
        ProviderHttp.Response response = http.get(provider(), properties.wecomApiBaseUrl(), "/cgi-bin/auth/getuserinfo",
                Map.of("access_token", token, "code", code), Map.of());
        int errcode = errcode(response, "/cgi-bin/auth/getuserinfo");
        if (errcode == INVALID_CODE) {
            throw new OrgProviderException(Kind.CODE_REJECTED, "wecom getuserinfo: errcode " + errcode);
        }
        if (errcode != 0) {
            expireTokenIfNeeded(connection, secret, errcode);
            throw ProviderHttp.unavailable(provider(), "/cgi-bin/auth/getuserinfo", "errcode " + errcode);
        }
        String userId = response.text("userid");
        if (userId == null || userId.isBlank() || userId.contains("/")) {
            // 只有 openid（非企业成员）或上下游互联的 "CorpId/userid"：都不是本企业成员。
            throw new OrgProviderException(Kind.NOT_A_MEMBER, "wecom getuserinfo: not a member of the corp");
        }
        return new ProviderUser(connection.corpId(), userId);
    }

    @Override
    public DirectoryStatus lookup(OrgConnection connection, String secret, String userId) {
        try {
            String token = accessToken(connection, secret);
            ProviderHttp.Response response = http.get(provider(), properties.wecomApiBaseUrl(), "/cgi-bin/user/get",
                    Map.of("access_token", token, "userid", userId), Map.of());
            int errcode = errcode(response, "/cgi-bin/user/get");
            if (errcode == USER_NOT_FOUND) {
                return DirectoryStatus.DEPARTED;
            }
            if (errcode != 0) {
                expireTokenIfNeeded(connection, secret, errcode);
                log.warn("wecom user/get failed: errcode {}", errcode);
                return DirectoryStatus.UNKNOWN;
            }
            return switch (response.intValue("status", -1)) {
                case STATUS_ACTIVE, STATUS_NOT_ACTIVATED -> DirectoryStatus.ACTIVE;
                case STATUS_DISABLED, STATUS_LEFT -> DirectoryStatus.DEPARTED;
                default -> DirectoryStatus.UNKNOWN;
            };
        } catch (OrgProviderException e) {
            log.warn("wecom directory lookup inconclusive: {}", e.getMessage());
            return DirectoryStatus.UNKNOWN;
        }
    }

    private String accessToken(OrgConnection connection, String secret) {
        return tokens.get(connection, secret, () -> {
            ProviderHttp.Response response = http.get(provider(), properties.wecomApiBaseUrl(), "/cgi-bin/gettoken",
                    Map.of("corpid", connection.corpId(), "corpsecret", secret), Map.of());
            int errcode = errcode(response, "/cgi-bin/gettoken");
            if (BAD_CREDENTIALS.contains(errcode)) {
                throw new OrgProviderException(Kind.CREDENTIALS_REJECTED, "wecom gettoken: errcode " + errcode);
            }
            String token = response.text("access_token");
            if (errcode != 0 || token == null) {
                throw ProviderHttp.unavailable(provider(), "/cgi-bin/gettoken", "errcode " + errcode);
            }
            return new AppTokenCache.AppToken(token, Duration.ofSeconds(response.intValue("expires_in", 7200)));
        });
    }

    private void expireTokenIfNeeded(OrgConnection connection, String secret, int errcode) {
        if (TOKEN_EXPIRED.contains(errcode)) {
            tokens.invalidate(connection, secret);
        }
    }

    private int errcode(ProviderHttp.Response response, String path) {
        if (!response.ok()) {
            throw ProviderHttp.unavailable(provider(), path, "http " + response.status());
        }
        return response.intValue("errcode", -1);
    }
}
