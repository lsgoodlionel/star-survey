package cn.mjy.platform.identity.org;

import cn.mjy.platform.identity.org.OrgProviderException.Kind;
import java.net.URI;
import java.time.Duration;
import java.util.LinkedHashMap;
import java.util.Map;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Component;
import tools.jackson.databind.JsonNode;

/**
 * 钉钉（企业内部应用，新版 OAuth2 统一登录）。文档：
 * <ul>
 *   <li>登录第三方网站（授权页 login.dingtalk.com/oauth2/auth）
 *       https://open.dingtalk.com/document/orgapp/tutorial-obtaining-user-personal-information</li>
 *   <li>获取用户 token（POST /v1.0/oauth2/userAccessToken）https://open.dingtalk.com/document/orgapp/obtain-user-token</li>
 *   <li>获取用户通讯录个人信息（GET /v1.0/contact/users/me）
 *       https://open.dingtalk.com/document/orgapp/dingtalk-retrieve-user-information</li>
 *   <li>获取企业内部应用 accessToken（POST /v1.0/oauth2/accessToken）
 *       https://open.dingtalk.com/document/orgapp/obtain-the-access_token-of-an-internal-app</li>
 *   <li>根据 unionid 获取 userid（topapi/user/getbyunionid）https://open.dingtalk.com/document/orgapp/query-a-user-by-the-union-id</li>
 *   <li>查询用户详情（topapi/v2/user/get，找不到 60121）https://open.dingtalk.com/document/orgapp/query-user-details</li>
 * </ul>
 * 新版接口出错时返回 4xx + {code, message}；旧版 oapi 返回 HTTP 200 + errcode。
 * 用户授权时所选组织由 userAccessToken 的 corpId 给出，必须等于连接配置的 corpId。
 */
@Component
class DingTalkClient implements OrgPlatformClient {

    private static final Logger log = LoggerFactory.getLogger(DingTalkClient.class);

    static final int USER_NOT_FOUND = 60121;
    private static final String TOKEN_HEADER = "x-acs-dingtalk-access-token";
    private static final String BAD_CLIENT = "invalidClientIdOrSecret";
    private static final int INTERNAL_CONTACT = 0;
    private static final java.util.Set<Integer> TOKEN_EXPIRED = java.util.Set.of(88, 40014, 42001);

    private final ProviderHttp http;
    private final AppTokenCache tokens;
    private final OrgLoginProperties properties;

    DingTalkClient(ProviderHttp http, AppTokenCache tokens, OrgLoginProperties properties) {
        this.http = http;
        this.tokens = tokens;
        this.properties = properties;
    }

    @Override
    public OrgProvider provider() {
        return OrgProvider.DINGTALK;
    }

    @Override
    public URI authorizeUrl(OrgConnection connection, String redirectUri, String state, boolean qr) {
        Map<String, String> query = new LinkedHashMap<>();
        query.put("redirect_uri", redirectUri);
        query.put("response_type", "code");
        query.put("client_id", connection.appId());
        query.put("scope", "openid corpid");
        query.put("state", state);
        query.put("prompt", "consent");
        // 限定只能以本组织身份授权（用户不在该组织时授权页不给出该组织）。
        query.put("corpId", connection.corpId());
        return URI.create(properties.dingtalkAuthorizeUrl() + "?" + ProviderHttp.queryString(query));
    }

    @Override
    public ProviderUser exchange(OrgConnection connection, String secret, String code, String redirectUri) {
        ProviderHttp.Response token = http.postJson(provider(), properties.dingtalkApiBaseUrl(),
                "/v1.0/oauth2/userAccessToken", Map.of(), Map.of(),
                Map.of("clientId", connection.appId(), "clientSecret", secret, "code", code,
                        "grantType", "authorization_code"));
        rejectFailedOauth(token, "/v1.0/oauth2/userAccessToken");
        String userToken = token.text("accessToken");
        if (userToken == null) {
            throw ProviderHttp.unavailable(provider(), "/v1.0/oauth2/userAccessToken", "no accessToken");
        }
        if (!connection.corpId().equals(token.text("corpId"))) {
            throw new OrgProviderException(Kind.WRONG_ORGANISATION, "dingtalk: authorised in another organisation");
        }
        ProviderHttp.Response me = http.get(provider(), properties.dingtalkApiBaseUrl(), "/v1.0/contact/users/me",
                Map.of(), Map.of(TOKEN_HEADER, userToken));
        if (!me.ok() || me.text("unionId") == null) {
            throw ProviderHttp.unavailable(provider(), "/v1.0/contact/users/me", "http " + me.status());
        }
        return new ProviderUser(connection.corpId(), userIdOf(connection, secret, me.text("unionId")));
    }

    @Override
    public DirectoryStatus lookup(OrgConnection connection, String secret, String userId) {
        try {
            ProviderHttp.Response response = oapi(connection, secret, "/topapi/v2/user/get", Map.of("userid", userId));
            int errcode = response.intValue("errcode", -1);
            if (errcode == USER_NOT_FOUND) {
                return DirectoryStatus.DEPARTED;
            }
            if (errcode != 0) {
                log.warn("dingtalk user/get failed: errcode {}", errcode);
                return DirectoryStatus.UNKNOWN;
            }
            return DirectoryStatus.ACTIVE;
        } catch (OrgProviderException e) {
            log.warn("dingtalk directory lookup inconclusive: {}", e.getMessage());
            return DirectoryStatus.UNKNOWN;
        }
    }

    private String userIdOf(OrgConnection connection, String secret, String unionId) {
        ProviderHttp.Response response = oapi(connection, secret, "/topapi/user/getbyunionid", Map.of("unionid", unionId));
        int errcode = response.intValue("errcode", -1);
        if (errcode == USER_NOT_FOUND) {
            throw new OrgProviderException(Kind.NOT_A_MEMBER, "dingtalk getbyunionid: not a member of the corp");
        }
        JsonNode result = response.body().get("result");
        if (errcode != 0 || result == null || result.get("userid") == null) {
            throw ProviderHttp.unavailable(provider(), "/topapi/user/getbyunionid", "errcode " + errcode);
        }
        JsonNode contactType = result.get("contact_type");
        if (contactType != null && contactType.asInt() != INTERNAL_CONTACT) {
            throw new OrgProviderException(Kind.NOT_A_MEMBER, "dingtalk getbyunionid: external contact");
        }
        return result.get("userid").asString();
    }

    private ProviderHttp.Response oapi(OrgConnection connection, String secret, String path, Map<String, String> body) {
        String token = appToken(connection, secret);
        ProviderHttp.Response response = http.postJson(provider(), properties.dingtalkOapiBaseUrl(), path,
                Map.of("access_token", token), Map.of(), body);
        if (!response.ok()) {
            throw ProviderHttp.unavailable(provider(), path, "http " + response.status());
        }
        if (TOKEN_EXPIRED.contains(response.intValue("errcode", -1))) {
            tokens.invalidate(connection, secret);
        }
        return response;
    }

    private String appToken(OrgConnection connection, String secret) {
        return tokens.get(connection, secret, () -> {
            ProviderHttp.Response response = http.postJson(provider(), properties.dingtalkApiBaseUrl(),
                    "/v1.0/oauth2/accessToken", Map.of(), Map.of(),
                    Map.of("appKey", connection.appId(), "appSecret", secret));
            rejectFailedOauth(response, "/v1.0/oauth2/accessToken");
            String token = response.text("accessToken");
            if (token == null) {
                throw ProviderHttp.unavailable(provider(), "/v1.0/oauth2/accessToken", "no accessToken");
            }
            return new AppTokenCache.AppToken(token, Duration.ofSeconds(response.intValue("expireIn", 7200)));
        });
    }

    /** 新版接口：2xx 成功；4xx 按错误码区分凭据错误与授权码错误；其余视为不可用。 */
    private void rejectFailedOauth(ProviderHttp.Response response, String path) {
        int status = response.status();
        if (status >= 200 && status < 300) {
            return;
        }
        String code = response.text("code");
        if (status >= 400 && status < 500 && BAD_CLIENT.equals(code)) {
            throw new OrgProviderException(Kind.CREDENTIALS_REJECTED, "dingtalk " + path + ": " + code);
        }
        if (status >= 400 && status < 500 && code != null) {
            throw new OrgProviderException(Kind.CODE_REJECTED, "dingtalk " + path + ": " + code);
        }
        throw ProviderHttp.unavailable(provider(), path, "http " + status);
    }
}
