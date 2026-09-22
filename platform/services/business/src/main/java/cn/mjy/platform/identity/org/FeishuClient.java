package cn.mjy.platform.identity.org;

import cn.mjy.platform.identity.org.OrgProviderException.Kind;
import java.net.URI;
import java.time.Duration;
import java.util.LinkedHashMap;
import java.util.Map;
import java.util.Set;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Component;
import tools.jackson.databind.JsonNode;

/**
 * 飞书（企业自建应用，OAuth 2.0 授权码）。文档：
 * <ul>
 *   <li>获取授权码 https://open.feishu.cn/document/common-capabilities/sso/api/obtain-oauth-code</li>
 *   <li>获取 user_access_token（POST /open-apis/authen/v2/oauth/token）
 *       https://open.feishu.cn/document/authentication-management/access-token/get-user-access-token</li>
 *   <li>获取用户信息（GET /open-apis/authen/v1/user_info）
 *       https://open.feishu.cn/document/server-docs/authentication-management/login-state-management/get</li>
 *   <li>自建应用获取 tenant_access_token https://open.feishu.cn/document/server-docs/authentication-management/access-token/tenant_access_token_internal</li>
 *   <li>获取单个用户信息（GET /open-apis/contact/v3/users/:user_id）https://open.feishu.cn/document/server-docs/contact-v3/user/get</li>
 * </ul>
 * 主体用 user_id（租户内唯一，需开通"获取用户 user ID"权限）而不是 open_id（只在一个应用内唯一）；
 * user_info 的 tenant_key 必须等于连接配置的组织。
 */
@Component
class FeishuClient implements OrgPlatformClient {

    private static final Logger log = LoggerFactory.getLogger(FeishuClient.class);

    private static final int BAD_CLIENT = 20002;
    /** 20001 参数缺失、20003 授权码无效或已用、20004 已过期、20010 用户无应用权限、20024 码不属于本应用。 */
    private static final Set<Integer> CODE_ERRORS = Set.of(20001, 20003, 20004, 20010, 20024);

    /** tenant_access_token 无效或过期。 */
    private static final Set<Integer> TOKEN_EXPIRED = Set.of(99991663, 99991664);

    private final ProviderHttp http;
    private final AppTokenCache tokens;
    private final OrgLoginProperties properties;

    FeishuClient(ProviderHttp http, AppTokenCache tokens, OrgLoginProperties properties) {
        this.http = http;
        this.tokens = tokens;
        this.properties = properties;
    }

    @Override
    public OrgProvider provider() {
        return OrgProvider.FEISHU;
    }

    @Override
    public URI authorizeUrl(OrgConnection connection, String redirectUri, String state, boolean qr) {
        Map<String, String> query = new LinkedHashMap<>();
        query.put("client_id", connection.appId());
        query.put("response_type", "code");
        query.put("redirect_uri", redirectUri);
        query.put("state", state);
        return URI.create(properties.feishuAuthorizeUrl() + "?" + ProviderHttp.queryString(query));
    }

    @Override
    public ProviderUser exchange(OrgConnection connection, String secret, String code, String redirectUri) {
        ProviderHttp.Response token = http.postJson(provider(), properties.feishuApiBaseUrl(),
                "/open-apis/authen/v2/oauth/token", Map.of(), Map.of(),
                Map.of("grant_type", "authorization_code", "client_id", connection.appId(), "client_secret", secret,
                        "code", code, "redirect_uri", redirectUri));
        int result = token.intValue("code", -1);
        if (result == BAD_CLIENT) {
            throw new OrgProviderException(Kind.CREDENTIALS_REJECTED, "feishu oauth/token: code " + result);
        }
        if (CODE_ERRORS.contains(result)) {
            throw new OrgProviderException(Kind.CODE_REJECTED, "feishu oauth/token: code " + result);
        }
        String userToken = token.text("access_token");
        if (!token.ok() || result != 0 || userToken == null) {
            throw ProviderHttp.unavailable(provider(), "/open-apis/authen/v2/oauth/token",
                    "http " + token.status() + " code " + result);
        }
        JsonNode data = userInfo(userToken);
        if (!connection.corpId().equals(text(data, "tenant_key"))) {
            throw new OrgProviderException(Kind.WRONG_ORGANISATION, "feishu: authorised in another tenant");
        }
        String userId = text(data, "user_id");
        if (userId == null || userId.isBlank()) {
            throw new OrgProviderException(Kind.INCOMPLETE_IDENTITY,
                    "feishu user_info has no user_id; grant the app permission to read user IDs");
        }
        return new ProviderUser(connection.corpId(), userId);
    }

    @Override
    public DirectoryStatus lookup(OrgConnection connection, String secret, String userId) {
        try {
            String token = tenantToken(connection, secret);
            ProviderHttp.Response response = http.get(provider(), properties.feishuApiBaseUrl(),
                    "/open-apis/contact/v3/users/" + ProviderHttp.encode(userId), Map.of("user_id_type", "user_id"),
                    Map.of("Authorization", "Bearer " + token));
            int result = response.intValue("code", -1);
            JsonNode status = response.body().path("data").path("user").path("status");
            if (!response.ok() || result != 0 || !status.isObject()) {
                if (TOKEN_EXPIRED.contains(result)) {
                    tokens.invalidate(connection, secret);
                }
                log.warn("feishu contact user get inconclusive: http {} code {}", response.status(), result);
                return DirectoryStatus.UNKNOWN;
            }
            boolean gone = status.path("is_resigned").asBoolean(false) || status.path("is_frozen").asBoolean(false);
            return gone ? DirectoryStatus.DEPARTED : DirectoryStatus.ACTIVE;
        } catch (OrgProviderException e) {
            log.warn("feishu directory lookup inconclusive: {}", e.getMessage());
            return DirectoryStatus.UNKNOWN;
        }
    }

    private JsonNode userInfo(String userToken) {
        ProviderHttp.Response info = http.get(provider(), properties.feishuApiBaseUrl(), "/open-apis/authen/v1/user_info",
                Map.of(), Map.of("Authorization", "Bearer " + userToken));
        JsonNode data = info.body().get("data");
        if (!info.ok() || info.intValue("code", -1) != 0 || data == null || !data.isObject()) {
            throw ProviderHttp.unavailable(provider(), "/open-apis/authen/v1/user_info",
                    "http " + info.status() + " code " + info.intValue("code", -1));
        }
        return data;
    }

    private String tenantToken(OrgConnection connection, String secret) {
        return tokens.get(connection, secret, () -> {
            ProviderHttp.Response response = http.postJson(provider(), properties.feishuApiBaseUrl(),
                    "/open-apis/auth/v3/tenant_access_token/internal", Map.of(), Map.of(),
                    Map.of("app_id", connection.appId(), "app_secret", secret));
            String token = response.text("tenant_access_token");
            if (response.intValue("code", -1) != 0 || token == null) {
                throw ProviderHttp.unavailable(provider(), "/open-apis/auth/v3/tenant_access_token/internal",
                        "http " + response.status() + " code " + response.intValue("code", -1));
            }
            return new AppTokenCache.AppToken(token, Duration.ofSeconds(response.intValue("expire", 7200)));
        });
    }

    private static String text(JsonNode node, String field) {
        JsonNode value = node.get(field);
        return value == null || value.isNull() ? null : value.asString();
    }
}
