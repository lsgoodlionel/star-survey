package cn.mjy.platform.identity.org;

import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.put;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

import cn.mjy.platform.access.MemberService;
import cn.mjy.platform.shared.TenantId;
import cn.mjy.platform.shared.tenant.TenantScope;
import cn.mjy.platform.support.FakeSeatAllowance;
import cn.mjy.platform.support.TestTokens;
import org.springframework.jdbc.core.simple.JdbcClient;
import cn.mjy.platform.tenant.TenantFixtures;
import com.jayway.jsonpath.JsonPath;
import jakarta.servlet.http.Cookie;
import java.net.URI;
import java.net.URLDecoder;
import java.nio.charset.StandardCharsets;
import java.util.Arrays;
import java.util.List;
import java.util.Map;
import java.util.UUID;
import java.util.stream.Collectors;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.webmvc.test.autoconfigure.AutoConfigureMockMvc;
import org.springframework.http.MediaType;
import org.springframework.test.context.DynamicPropertyRegistry;
import org.springframework.test.context.DynamicPropertySource;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.test.web.servlet.MvcResult;
import org.springframework.test.web.servlet.ResultActions;

/**
 * 免登测试的公共部分：把三家开放平台的地址指向本地替身，开通带所有者的租户，走 API 建连接、发起登录、回调。
 * 所有子类共用同一组配置，因此共用同一个 Spring 上下文。
 */
@SpringBootTest
@AutoConfigureMockMvc
abstract class OrgLoginTestSupport {

    static final String OWNER = "owner";
    static final String CALLBACK_BASE = "https://survey.example.test";

    static final FakeOrgPlatforms FAKE = FakeOrgPlatforms.get();

    @DynamicPropertySource
    static void orgPlatforms(DynamicPropertyRegistry registry) {
        String base = FAKE.baseUrl();
        registry.add("platform.identity.org-login.callback-base-url", () -> CALLBACK_BASE);
        registry.add("platform.identity.org-login.wecom-api-base-url", () -> base + "/wecom");
        registry.add("platform.identity.org-login.dingtalk-api-base-url", () -> base + "/dingtalk");
        registry.add("platform.identity.org-login.dingtalk-oapi-base-url", () -> base + "/dingtalk-oapi");
        registry.add("platform.identity.org-login.feishu-api-base-url", () -> base + "/feishu");
        // 小页，让同步测试真正跨页。
        registry.add("platform.identity.org-sync.batch-size", () -> SYNC_BATCH_SIZE);
    }

    static final int SYNC_BATCH_SIZE = 2;

    @Autowired
    MockMvc mvc;

    @Autowired
    TenantFixtures tenants;

    @Autowired
    MemberService members;

    @Autowired
    TestTokens tokens;

    @Autowired
    FakeSecretStore secrets;

    @Autowired
    FakeSeatAllowance seats;

    @Autowired
    TenantScope tenantScope;

    @Autowired
    JdbcClient jdbc;

    /** 已开通、带所有者（actor = owner）的租户。 */
    TenantId newTenant() {
        TenantId tenant = tenants.activeTenant();
        members.bootstrapOwner(tenant, OWNER, "trace-bootstrap");
        return tenant;
    }

    String ownerBearer(TenantId tenant) {
        return bearer(tenant, OWNER);
    }

    String bearer(TenantId tenant, String actor) {
        return "Bearer " + tokens.issue(actor, tenant.value(), List.of());
    }

    static String unique(String prefix) {
        return prefix + UUID.randomUUID().toString().replace("-", "").substring(0, 12);
    }

    /** 一个组织连接的测试上下文：在替身里登记应用、在密钥库里放密钥、经 API 建连接。 */
    record Org(TenantId tenant, String provider, String corp, String appId, String secret, String secretRef,
            UUID connectionId) {
    }

    Org connect(TenantId tenant, String provider, String policy) throws Exception {
        return connect(tenant, provider, unique("corp"), unique("app"), policy);
    }

    Org connect(TenantId tenant, String provider, String corp, String appId, String policy) throws Exception {
        return connect(tenant, provider, corp, appId, "s3cr3t-" + UUID.randomUUID(), policy);
    }

    /** 同一个开放平台应用只有一个密钥；两个租户接同一个应用时传入同一个 secret。 */
    Org connect(TenantId tenant, String provider, String corp, String appId, String secret, String policy)
            throws Exception {
        String secretRef = unique("APP_" + provider + "_").toUpperCase();
        FAKE.registerApp(provider, corp, appId, secret);
        secrets.put(tenant, secretRef, secret);
        String body = createConnection(tenant, provider, corp, appId, secretRef, policy)
                .andExpect(status().isCreated()).andReturn().getResponse().getContentAsString();
        return new Org(tenant, provider, corp, appId, secret, secretRef,
                UUID.fromString(JsonPath.read(body, "$.id")));
    }

    ResultActions createConnection(TenantId tenant, String provider, String corp, String appId, String secretRef,
            String policy) throws Exception {
        String policyField = policy == null ? "" : ",\"unknownUserPolicy\":\"" + policy + "\"";
        return mvc.perform(post("/v1/org-connections")
                .header("Authorization", ownerBearer(tenant))
                .contentType(MediaType.APPLICATION_JSON)
                .content("{\"provider\":\"%s\",\"corpId\":\"%s\",\"appId\":\"%s\",\"secretRef\":\"%s\"%s}"
                        .formatted(provider, corp, appId, secretRef, policyField)));
    }

    /** 管理员预先授权某个组织成员（绑定身份并按员工邀请，占一个席位）。 */
    String preauthorize(Org org, String userId) throws Exception {
        String body = mvc.perform(post("/v1/org-connections/" + org.connectionId() + "/authorized-users")
                        .header("Authorization", ownerBearer(org.tenant()))
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"externalId\":\"" + userId + "\"}"))
                .andExpect(status().isCreated()).andReturn().getResponse().getContentAsString();
        return JsonPath.read(body, "$.principalId");
    }

    /** 发起登录：返回的 state 取自跳转地址，cookie 是浏览器绑定。 */
    record Started(String state, Cookie browser, URI location) {
    }

    Started start(TenantId tenant, UUID connectionId) throws Exception {
        MvcResult result = mvc.perform(get(startPath(tenant, connectionId)))
                .andExpect(status().isFound()).andReturn();
        URI location = URI.create(result.getResponse().getHeader("Location"));
        Cookie cookie = result.getResponse().getCookie(OrgLoginController.BROWSER_COOKIE);
        return new Started(query(location).get("state"), cookie, location);
    }

    ResultActions callback(TenantId tenant, UUID connectionId, String code, Started started) throws Exception {
        var request = get(callbackPath(tenant, connectionId)).param("code", code).param("state", started.state());
        if (started.browser() != null) {
            request = request.cookie(started.browser());
        }
        return mvc.perform(request);
    }

    /** 完整一轮免登，返回平台令牌。 */
    String login(Org org, String userId) throws Exception {
        Started started = start(org.tenant(), org.connectionId());
        String code = FAKE.issueCode(org.provider(), org.corp(), org.appId(), userId);
        String body = callback(org.tenant(), org.connectionId(), code, started)
                .andExpect(status().isOk()).andReturn().getResponse().getContentAsString();
        return JsonPath.read(body, "$.accessToken");
    }

    /** 令牌的 sub（主体标识）；只解析载荷，不验签——验签由被测应用负责。 */
    static String subject(String token) {
        String payload = new String(java.util.Base64.getUrlDecoder().decode(token.split("\\.")[1]),
                StandardCharsets.UTF_8);
        return JsonPath.read(payload, "$.sub");
    }

    void expireState(TenantId tenant, String state) {
        tenantScope.run(tenant, () -> jdbc.sql(
                        "UPDATE org_login_state SET expires_at = now() - interval '1 second' WHERE state = :s")
                .param("s", state).update());
    }

    /** 连接的事件订阅密钥：token 与加密密钥（企业微信 / 钉钉为 43 位 EncodingAESKey，飞书为任意 Encrypt Key）。 */
    record EventSecrets(String token, String key) {
    }

    /** 给连接配置事件订阅：在密钥库里放 Token 与加密密钥，经 API 登记两个密钥名。 */
    EventSecrets subscribeEvents(Org org) throws Exception {
        byte[] raw = new byte[32];
        new java.security.SecureRandom().nextBytes(raw);
        String key = "feishu".equals(org.provider()) ? "ek-" + UUID.randomUUID()
                : java.util.Base64.getEncoder().encodeToString(raw).substring(0, 43);
        EventSecrets secretsOfOrg = new EventSecrets("tk" + UUID.randomUUID().toString().replace("-", ""), key);
        String tokenRef = unique("EVT_TOKEN_").toUpperCase();
        String keyRef = unique("EVT_KEY_").toUpperCase();
        secrets.put(org.tenant(), tokenRef, secretsOfOrg.token());
        secrets.put(org.tenant(), keyRef, secretsOfOrg.key());
        mvc.perform(put("/v1/org-connections/" + org.connectionId())
                        .header("Authorization", ownerBearer(org.tenant()))
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"eventTokenRef\":\"%s\",\"eventKeyRef\":\"%s\"}".formatted(tokenRef, keyRef)))
                .andExpect(status().isOk());
        return secretsOfOrg;
    }

    static String eventPath(Org org) {
        return "/v1/org-events/" + org.tenant() + "/" + org.connectionId();
    }

    void disable(Org org) throws Exception {
        mvc.perform(put("/v1/org-connections/" + org.connectionId())
                        .header("Authorization", ownerBearer(org.tenant()))
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"enabled\":false}"))
                .andExpect(status().isOk());
    }

    static String startPath(TenantId tenant, UUID connectionId) {
        return "/v1/auth/org/" + tenant + "/" + connectionId + "/start";
    }

    static String callbackPath(TenantId tenant, UUID connectionId) {
        return "/v1/auth/org/" + tenant + "/" + connectionId + "/callback";
    }

    static Map<String, String> query(URI uri) {
        return Arrays.stream(uri.getRawQuery().split("&"))
                .map(pair -> pair.split("=", 2))
                .collect(Collectors.toMap(p -> p[0],
                        p -> p.length > 1 ? URLDecoder.decode(p[1], StandardCharsets.UTF_8) : "", (a, b) -> a));
    }
}
